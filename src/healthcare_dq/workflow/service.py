"""Explicit, session-owned advancement of a durable workflow."""

import logging
from time import perf_counter

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from healthcare_dq.db import connect
from healthcare_dq.quality.engine import digest

from . import stages

LOG = logging.getLogger(__name__)
LOCK_NAMESPACE = 404
RUNNER_NAMESPACE = 406
MAX_FAILURES = 3


class WorkflowError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def register(conn, request, request_key, resolve):
    input_hash = digest(request)
    with conn.transaction():
        # Serialize bindings of the same key independently of the execution slot.
        conn.execute("SELECT pg_advisory_xact_lock(405,hashtext(%s))", (request_key,))
        existing = conn.execute("SELECT request_sha256,workflow_id FROM workflow_request_key WHERE key=%s",
                                (request_key,)).fetchone()
        if existing is not None:
            if existing[0] != input_hash:
                raise WorkflowError("IDEMPOTENCY_KEY_CONFLICT")
            return existing[1], False
        spec = resolve(request).spec
        identity = digest(spec)
        inserted = conn.execute("""
            INSERT INTO workflow(execution_sha256,spec) VALUES(%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
        """, (identity, Jsonb(spec))).fetchone()
        if inserted:
            wid, = inserted
            conn.execute("""INSERT INTO workflow_stage(workflow_id,step)
                            SELECT %s,n FROM generate_series(0,4) AS n""", (wid,))
        else:
            wid, = conn.execute("SELECT id FROM workflow WHERE execution_sha256=%s", (identity,)).fetchone()
        conn.execute("INSERT INTO workflow_request_key VALUES(%s,%s,%s)", (request_key, input_hash, wid))
        return wid, bool(inserted)


def read(conn, wid):
    with conn.cursor(row_factory=dict_row) as cur:
        w = cur.execute("SELECT * FROM workflow WHERE id=%s", (wid,)).fetchone()
        if w is None:
            raise WorkflowError("WORKFLOW_NOT_FOUND")
        current = cur.execute("SELECT * FROM workflow_stage WHERE workflow_id=%s ORDER BY step",
                              (wid,)).fetchall()
    return w, current


def close_attempt(conn, wid, step, outcome, code=None, retryable=None):
    conn.execute("""UPDATE workflow_stage_attempt SET status=%s,code=%s,retryable=%s,finished_at=now()
                    WHERE workflow_id=%s AND step=%s AND status='running'""",
                 (outcome, code, retryable, wid, step))


def link(conn, wid, step, result):
    field = stages.RESULT_FIELDS[step]  # Fixed identifiers, independent of client input.
    close_attempt(conn, wid, step, "completed")
    conn.execute(f"""UPDATE workflow_stage SET status='completed',{field}=%s,updated_at=now()
                     WHERE workflow_id=%s AND step=%s""", (result, wid, step))
    LOG.info("stage_completed", extra={"workflow_id": wid, "stage": stages.NAMES[step]})


def after_execute():
    """Test seam at the independently committed stage boundary."""


def record_stop(conn, wid, step, failure=None, *, busy=False):
    if failure:
        code, retryable = failure
        outcome, status = "failed", "failed"
    else:
        code, retryable = ("STAGE_BUSY" if busy else "EXECUTOR_INTERRUPTED"), True
        outcome, status = ("deferred" if busy else "interrupted"), "interrupted"
    close_attempt(conn, wid, step, outcome, code, retryable)
    conn.execute("""UPDATE workflow_stage SET status=%s,updated_at=now()
                    WHERE workflow_id=%s AND step=%s""", (status, wid, step))
    LOG.warning("stage_stopped", extra={"workflow_id": wid, "stage": stages.NAMES[step], "code": code})


def recover(conn, wid, step, expected):
    # If just the owner connection died, the original runner may still be active.
    # Hold this transaction lock until the attempt has been reconciled.
    if not conn.execute("SELECT pg_try_advisory_xact_lock(%s,%s)", (RUNNER_NAMESPACE, wid)).fetchone()[0]:
        raise WorkflowError("EXECUTION_CAPACITY_BUSY")
    failure = stages.failure(conn, wid, step)
    if failure:
        record_stop(conn, wid, step, failure)
        return True
    found = stages.lookup(conn, step, expected)
    if found is not None:
        link(conn, wid, step, found)
        return True
    record_stop(conn, wid, step)
    return False


def advance(wid, resolve, connection=connect):
    with connection() as owner:
        read(owner, wid)
        # Global lock bounds the number of running stages across server processes.
        if not owner.execute("SELECT pg_try_advisory_lock(%s,0)", (LOCK_NAMESPACE,)).fetchone()[0]:
            raise WorkflowError("EXECUTION_CAPACITY_BUSY")
        if not owner.execute("SELECT pg_try_advisory_lock(%s,%s)", (LOCK_NAMESPACE, wid)).fetchone()[0]:
            raise WorkflowError("WORKFLOW_BUSY")
        workflow, progress = read(owner, wid)
        stage = next((s for s in progress if s["status"] != "completed"), None)
        if stage is None:
            return
        step = stage["step"]
        spec = workflow["spec"]
        expected = stages.key(spec, step, progress)
        with owner.transaction():
            if stage["status"] == "running":
                if recover(owner, wid, step, expected):
                    return
            elif stage["status"] == "failed":
                failures, retryable = owner.execute("""
                    SELECT count(*),bool_and(retryable) FROM workflow_stage_attempt
                    WHERE workflow_id=%s AND step=%s AND status='failed'
                """, (wid, step)).fetchone()
                if not retryable or failures >= MAX_FAILURES:
                    raise WorkflowError("STAGE_NOT_RETRYABLE")
        # Reconcile already committed outputs even if the artifact has since changed.
        found = stages.lookup(owner, step, expected)
        assets = None
        if found is None:
            try:
                assets = resolve({"left_fixture": spec["inputs"][0]["key"],
                                  "right_fixture": spec["inputs"][1]["key"], "profile": spec["profile"]})
            except WorkflowError as error:
                raise WorkflowError("ARTIFACT_UNAVAILABLE") from error
            if assets.spec != spec:
                raise WorkflowError("ARTIFACT_UNAVAILABLE")
        with owner.transaction():
            owner.execute("""UPDATE workflow_stage SET status='running',expected=%s,updated_at=now()
                             WHERE workflow_id=%s AND step=%s""", (Jsonb(expected), wid, step))
            attempt, = owner.execute("""INSERT INTO workflow_stage_attempt(workflow_id,step)
                                        VALUES(%s,%s) RETURNING id""", (wid, step)).fetchone()
            if found is not None:
                link(owner, wid, step, found)
                return
        LOG.info("stage_started", extra={"workflow_id": wid, "stage": stages.NAMES[step], "attempt": attempt})
        started = perf_counter()
        # Stage connections are short-lived and own their existing per-stage locks.
        with connection() as runner:
            locked, = runner.execute("SELECT pg_try_advisory_lock(%s,%s)", (RUNNER_NAMESPACE, wid)).fetchone()
            active = runner.execute("SELECT 1 FROM workflow_stage_attempt WHERE id=%s AND status='running'",
                                    (attempt,)).fetchone()
            if not locked or not active:
                raise WorkflowError("EXECUTION_CAPACITY_BUSY")
            runner.execute("SELECT set_config('healthcare_dq.workflow_attempt', %s, false)", (str(attempt),))
            result = stages.execute(runner, step, expected, assets)
        after_execute()
        LOG.info("stage_returned", extra={"workflow_id": wid, "stage": stages.NAMES[step],
                                         "attempt": attempt, "outcome": result["status"],
                                         "duration_ms": round((perf_counter() - started) * 1000, 3)})
        with owner.transaction():
            failure = stages.failure(owner, wid, step)
            found = stages.lookup(owner, step, expected)
            if failure:
                record_stop(owner, wid, step, failure)
            elif found is not None:
                link(owner, wid, step, found)
            elif result["status"] == "busy":
                record_stop(owner, wid, step, busy=True)
            else:
                raise RuntimeError("Stage returned without a durable outcome")
