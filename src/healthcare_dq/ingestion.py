"""Register, claim, and atomically publish one immutable ingestion batch."""

import hashlib
from collections import Counter
from time import perf_counter

import psycopg
import simplejson
from psycopg import sql
from psycopg.types.json import Jsonb

from .fhir import TABLES, prepare
from .inputs import InputError, manifest

CONTRACT = 1


def register(conn, source, files):
    digest = manifest(files)
    with conn.transaction():
        conn.execute("INSERT INTO ingestion_source(source_key) VALUES (%s) ON CONFLICT DO NOTHING",
                     (source,))
        source_id, = conn.execute("SELECT id FROM ingestion_source WHERE source_key=%s",
                                  (source,)).fetchone()
        created = conn.execute("""
            INSERT INTO ingestion_batch(source_id,manifest_sha256,contract_version)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id
        """, (source_id, digest, CONTRACT)).fetchone()
        if created is None:
            batch_id, = conn.execute("""
                SELECT id FROM ingestion_batch WHERE source_id=%s AND manifest_sha256=%s
                AND contract_version=%s
            """, (source_id, digest, CONTRACT)).fetchone()
        else:
            batch_id, = created
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO ingestion_file VALUES (%s,%s,%s,%s)",
                                [(batch_id, f.path, len(f.data), f.sha256) for f in files])
    return batch_id


def publish(conn, batch_id, entries):
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO raw_resource VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [
            (batch_id, e.path, e.index, e.alias, e.kind, e.identifier,
             Jsonb(e.payload, dumps=simplejson.dumps), "normalized" if e.kind in TABLES else "raw_only")
            for e in entries
        ])
        for kind, table in TABLES.items():
            rows = [dict(batch_id=batch_id, fhir_id=e.identifier, path=e.path,
                         entry_index=e.index, **e.fields) for e in entries if e.kind == kind]
            if rows:
                keys = tuple(rows[0])
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, keys)),
                    sql.SQL(",").join(sql.Placeholder() for _ in keys),
                )
                cur.executemany(query, [
                    tuple(Jsonb(v, dumps=simplejson.dumps) if isinstance(v, dict) else v
                          for v in row.values()) for row in rows
                ])


def status(conn, batch_id):
    row = conn.execute(
        "SELECT status,manifest_sha256,counts FROM ingestion_batch WHERE id=%s", (batch_id,),
    ).fetchone()
    if row is None:
        raise InputError("UNKNOWN_BATCH")
    attempts = conn.execute("""
        SELECT a.id,a.status,a.retryable,a.error_code,e.path,e.pointer
        FROM ingestion_attempt a LEFT JOIN ingestion_error e ON e.attempt_id=a.id
        WHERE a.batch_id=%s ORDER BY a.id
    """, (batch_id,)).fetchall()
    return {"batch_id": batch_id, "status": row[0], "manifest_sha256": row[1], "counts": row[2],
            "attempts": [dict(zip(("id", "status", "retryable", "code", "path", "pointer"), a))
                         for a in attempts]}


def claim(conn, batch_id):
    with conn.transaction():
        conn.execute("""
            UPDATE ingestion_attempt SET status='interrupted',finished_at=now(),
                retryable=true,error_code='INTERRUPTED'
            WHERE batch_id=%s AND status='running'
        """, (batch_id,))
        conn.execute("UPDATE ingestion_batch SET status='running' WHERE id=%s", (batch_id,))
        attempt, = conn.execute("INSERT INTO ingestion_attempt(batch_id) VALUES (%s) RETURNING id",
                                (batch_id,)).fetchone()
    return attempt


def fail(conn, batch_id, attempt, error, retryable):
    with conn.transaction():
        conn.execute("""
            UPDATE ingestion_attempt SET status='failed',finished_at=now(),retryable=%s,error_code=%s
            WHERE id=%s AND status='running'
        """, (retryable, error.code, attempt))
        conn.execute("INSERT INTO ingestion_error VALUES (%s,%s,%s,%s)",
                     (attempt, error.path, error.pointer, error.code))
        conn.execute("UPDATE ingestion_batch SET status='failed' WHERE id=%s", (batch_id,))


def ingest(conn, source, files, *, retry_batch=None):
    # The caller owns a fresh connection and closes it even when an outcome is uncertain.
    for file in files:
        if hashlib.sha256(file.data).hexdigest() != file.sha256:
            raise InputError("INPUT_CHECKSUM_MISMATCH", file.path)
    if retry_batch is None:
        batch_id = register(conn, source, files)
    else:
        batch_id = retry_batch
        row = conn.execute("""
            SELECT s.source_key,b.manifest_sha256,b.contract_version FROM ingestion_batch b
            JOIN ingestion_source s ON s.id=b.source_id WHERE b.id=%s
        """, (batch_id,)).fetchone()
        if row != (source, manifest(files), CONTRACT):
            raise InputError("RETRY_MANIFEST_MISMATCH")
    locked, = conn.execute("SELECT pg_try_advisory_lock(%s::bigint)", (batch_id,)).fetchone()
    if not locked:
        return {"batch_id": batch_id, "status": "busy"}
    result = status(conn, batch_id)
    if result["status"] == "published":
        return {**result, "replayed": True}
    attempt = claim(conn, batch_id)
    parse_start = perf_counter()
    try:
        entries = prepare(files)
    except InputError as error:
        fail(conn, batch_id, attempt, error, False)
        return status(conn, batch_id)
    parse_seconds = perf_counter() - parse_start
    kinds = Counter(e.kind for e in entries if e.kind in TABLES)
    counts = {"raw": len(entries), "normalized": sum(kinds.values()),
              "raw_only": len(entries) - sum(kinds.values()), "by_type": dict(kinds)}
    write_start = perf_counter()
    try:
        with conn.transaction():
            publish(conn, batch_id, entries)
            conn.execute("UPDATE ingestion_attempt SET status='published',finished_at=now() WHERE id=%s",
                         (attempt,))
            conn.execute("""
                UPDATE ingestion_batch SET status='published',published_at=now(),counts=%s WHERE id=%s
            """, (Jsonb(counts), batch_id))
    except (psycopg.OperationalError, psycopg.InterfaceError):
        # Includes unknown commit outcomes. Do not overwrite an outcome on this connection.
        raise
    except (psycopg.DatabaseError, ValueError, OverflowError):
        fail(conn, batch_id, attempt, InputError("PUBLICATION_FAILED"), True)
    return {**status(conn, batch_id), "timing_seconds": {
        "parse_and_resolve": parse_seconds, "publication": perf_counter() - write_start,
    }}
