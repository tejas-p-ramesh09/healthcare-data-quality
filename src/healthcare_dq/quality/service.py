"""Session-owned validation with atomic publication and durable attempts."""

import hashlib
from pathlib import Path
from time import perf_counter

import psycopg
from psycopg.types.json import Jsonb

from . import repository as repo
from .engine import evaluate


def engine_revision():
    # Hash the actual executable modules, including dirty worktree edits.
    root = Path(__file__).parent
    h = hashlib.sha256()
    for name in ("engine.py", "rules.py", "repository.py", "service.py"):
        h.update(name.encode() + b"\0" + (root / name).read_bytes())
    return h.hexdigest()


def validate(conn, batch_id, config):
    run_id = repo.register(conn, batch_id, config, engine_revision())
    locked, = conn.execute("SELECT pg_try_advisory_lock(%s::bigint)", (-run_id - 1,)).fetchone()
    if not locked:
        return {"id": run_id, "status": "busy"}
    row = repo.status(conn, run_id)
    if row["status"] == "completed":
        return {**row, "replayed": True}
    with conn.transaction():
        conn.execute("""
            UPDATE quality_attempt SET status='interrupted',finished_at=now(),error_code='INTERRUPTED'
            WHERE run_id=%s AND status='running'
        """, (run_id,))
        conn.execute("UPDATE quality_run SET status='running' WHERE id=%s", (run_id,))
        attempt, = conn.execute("INSERT INTO quality_attempt(run_id) VALUES (%s) RETURNING id",
                                (run_id,)).fetchone()
    try:
        start = perf_counter()
        records = repo.records(conn, batch_id)
        loaded = perf_counter()
        counts, findings = evaluate(records, config)
        evaluated = perf_counter()
        with conn.transaction():
            repo.publish(conn, run_id, batch_id, findings)
            conn.execute("UPDATE quality_attempt SET status='completed',finished_at=now() WHERE id=%s",
                         (attempt,))
            conn.execute("UPDATE quality_run SET status='completed',counts=%s,completed_at=now() WHERE id=%s",
                         (Jsonb(counts), run_id))
    except (psycopg.OperationalError, psycopg.InterfaceError):
        raise
    except Exception as error:  # noqa: BLE001 - persist a failed run for unexpected rule errors
        code = "VALIDATION_WRITE_FAILED" if isinstance(error, psycopg.Error) else "VALIDATION_ENGINE_FAILED"
        with conn.transaction():
            conn.execute("""
                UPDATE quality_attempt SET status='failed',finished_at=now(),error_code=%s WHERE id=%s
            """, (code, attempt))
            conn.execute("UPDATE quality_run SET status='failed' WHERE id=%s", (run_id,))
        return repo.status(conn, run_id)
    return {**repo.status(conn, run_id), "resources_scanned": len(records), "timing_seconds": {
        "read": loaded - start, "rules": evaluated - loaded, "publish": perf_counter() - evaluated,
    }}
