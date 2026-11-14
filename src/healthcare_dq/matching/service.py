"""Atomic PostgreSQL publication for the matching pipeline."""

import hashlib
import json
from pathlib import Path
from time import perf_counter

import psycopg
from psycopg.types.json import Jsonb

from .core import features
from .pipeline import rank, recommendations


def revision():
    h = hashlib.sha256()
    for name in ("core.py", "pipeline.py", "service.py"):
        h.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes())
    return h.hexdigest()


def register(conn, left, right, model):
    config = {"model": model, "revision": revision()}
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()
    with conn.transaction():
        if left == right or conn.execute(
            "SELECT count(*) FROM ingestion_batch WHERE id IN (%s,%s) AND status='published'",
            (left, right),
        ).fetchone()[0] != 2:
            raise ValueError("TWO_PUBLISHED_BATCHES_REQUIRED")
        conn.execute("""INSERT INTO matching_run(left_batch,right_batch,execution_sha256,configuration)
                        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (left, right, digest, Jsonb(config)))
        return conn.execute(
            "SELECT id FROM matching_run WHERE left_batch=%s AND right_batch=%s AND execution_sha256=%s",
            (left, right, digest),
        ).fetchone()[0]


def load(conn, batch):
    rows = conn.execute("""
        SELECT p.fhir_id,r.payload FROM patient p JOIN raw_resource r
        ON (p.batch_id,p.path,p.entry_index)=(r.batch_id,r.path,r.entry_index)
        WHERE p.batch_id=%s ORDER BY p.fhir_id LIMIT 5001
    """, (batch,)).fetchall()
    if len(rows) > 5000:
        raise ValueError("PATIENT_LIMIT")
    return {key: features(raw) for key, raw in rows}


def status(conn, rid):
    row = conn.execute("SELECT status,counts FROM matching_run WHERE id=%s", (rid,)).fetchone()
    if row is None:
        raise ValueError("UNKNOWN_MATCHING_RUN")
    attempts = conn.execute(
        "SELECT id,status,error_code FROM matching_attempt WHERE run_id=%s ORDER BY id", (rid,),
    ).fetchall()
    return {"id": rid, "status": row[0], "counts": row[1], "attempts": attempts}


def publish(conn, rid, left, right, rows, decisions):
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO matching_candidate VALUES (%s,%s,%s,%s,%s,%s)",
                        [(rid, left, right, l, r, Jsonb(row["blocks"])) for (l, r), row in rows.items()])
        for method, items in decisions.items():
            cur.executemany("INSERT INTO matching_score VALUES (%s,%s,%s,%s,%s,%s)", [
                (rid, l, r, method, row[method], Jsonb({"levels": row["levels"],
                 "contributions": row["contributions"] if method == "statistical" else {}}))
                for (l, r), row in rows.items()
            ])
            cur.executemany("INSERT INTO matching_recommendation VALUES (%s,%s,%s,%s,%s,%s)",
                            [(rid, l, r, method, *v) for (l, r), v in items.items()])


def run(conn, left, right, model):
    rid = register(conn, left, right, model)
    if not conn.execute("SELECT pg_try_advisory_lock(303,%s::integer)", (rid,)).fetchone()[0]:
        return {"id": rid, "status": "busy"}
    current = status(conn, rid)
    if current["status"] == "completed":
        return {**current, "replayed": True}
    with conn.transaction():
        conn.execute("""UPDATE matching_attempt SET status='interrupted',finished_at=now(),
                        error_code='INTERRUPTED' WHERE run_id=%s AND status='running'""", (rid,))
        conn.execute("UPDATE matching_run SET status='running' WHERE id=%s", (rid,))
        attempt, = conn.execute("INSERT INTO matching_attempt(run_id) VALUES(%s) RETURNING id",
                                (rid,)).fetchone()
    try:
        start = perf_counter()
        a, b = load(conn, left), load(conn, right)
        loaded = perf_counter()
        rows, emissions, stage = rank(a, b, model, timings=True)
        decisions = {m: recommendations(rows, m, p) for m, p in model["policies"].items()}
        ranked = perf_counter()
        counts = {"left": len(a), "right": len(b), "candidates": len(rows),
                  "block_emissions": emissions, "no_candidate_left": len(a.keys() - {l for l, _ in rows}),
                  "no_candidate_right": len(b.keys() - {r for _, r in rows})}
        with conn.transaction():
            publish(conn, rid, left, right, rows, decisions)
            conn.execute("UPDATE matching_run SET status='completed',counts=%s,finished_at=now() WHERE id=%s",
                         (Jsonb(counts), rid))
            conn.execute("UPDATE matching_attempt SET status='completed',finished_at=now() WHERE id=%s",
                         (attempt,))
    except (psycopg.OperationalError, psycopg.InterfaceError):
        raise
    except (ValueError, psycopg.DatabaseError) as error:
        code = str(error).split(":")[0] if isinstance(error, ValueError) else "MATCHING_WRITE_FAILED"
        if code not in {"PATIENT_LIMIT", "OVERSIZED_BLOCK", "CANDIDATE_LIMIT", "MATCHING_WRITE_FAILED"}:
            code = "MATCHING_FAILED"
        with conn.transaction():
            conn.execute("UPDATE matching_run SET status='failed' WHERE id=%s", (rid,))
            conn.execute("UPDATE matching_attempt SET status='failed',finished_at=now(),error_code=%s WHERE id=%s",
                         (code, attempt))
        return status(conn, rid)
    return {**status(conn, rid), "seconds": {"read": loaded - start, **stage,
            "decision": ranked - loaded - sum(stage.values()), "publish": perf_counter() - ranked}}


def no_candidates(conn, rid):
    return conn.execute("""
        SELECT side,batch,id FROM (
          SELECT 'left' AS side,p.batch_id AS batch,p.fhir_id AS id FROM matching_run r
          JOIN patient p ON p.batch_id=r.left_batch WHERE r.id=%s AND r.status='completed'
          AND NOT EXISTS (SELECT FROM matching_candidate c WHERE c.run_id=r.id AND c.left_id=p.fhir_id)
          UNION ALL
          SELECT 'right',p.batch_id,p.fhir_id FROM matching_run r
          JOIN patient p ON p.batch_id=r.right_batch WHERE r.id=%s AND r.status='completed'
          AND NOT EXISTS (SELECT FROM matching_candidate c WHERE c.run_id=r.id AND c.right_id=p.fhir_id)
        ) result ORDER BY side,id
    """, (rid, rid)).fetchall()
