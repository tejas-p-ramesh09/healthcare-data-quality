"""Adapters to existing stage contracts and durable result keys."""

from healthcare_dq import ingestion
from healthcare_dq.matching import service as matching
from healthcare_dq.quality import service as quality
from healthcare_dq.quality.engine import digest

NAMES = ("ingest_left", "ingest_right", "validate_left", "validate_right", "match")
RESULT_FIELDS = ("batch_id", "batch_id", "quality_id", "quality_id", "matching_id")


def key(workflow, step, prior):
    if step < 2:
        return [workflow["inputs"][step]["source"], workflow["inputs"][step]["manifest"],
                workflow["ingestion_contract"]]
    if step < 4:
        return [prior[step - 2]["batch_id"], workflow["ruleset_sha256"], workflow["quality_revision"]]
    return [prior[0]["batch_id"], prior[1]["batch_id"], workflow["matching_execution"]]


def revisions(config, model):
    return {"ingestion_contract": ingestion.CONTRACT, "ruleset_sha256": digest(config),
            "quality_revision": quality.engine_revision(), "model_sha256": digest(model),
            "matching_execution": matching.execution_identity(model)[1]}


def lookup(conn, step, expected):
    if step < 2:
        sql = """SELECT b.id FROM ingestion_batch b JOIN ingestion_source s ON s.id=b.source_id
                 WHERE s.source_key=%s AND b.manifest_sha256=%s AND b.contract_version=%s
                 AND b.status='published'"""
    elif step < 4:
        sql = """SELECT id FROM quality_run WHERE batch_id=%s AND ruleset_sha256=%s AND engine_revision=%s
                 AND status='completed'"""
    else:
        sql = """SELECT id FROM matching_run WHERE left_batch=%s AND right_batch=%s AND execution_sha256=%s
                 AND status='completed'"""
    row = conn.execute(sql, expected).fetchone()
    return row[0] if row else None


def execute(conn, step, expected, assets):
    if step < 2:
        return ingestion.ingest(conn, expected[0], assets.files[step])
    if step < 4:
        return quality.validate(conn, expected[0], assets.rules)
    return matching.run(conn, *expected[:2], assets.model)


def failure(conn, workflow_id, step):
    """Read only the terminal failure attributed to this workflow attempt."""
    row = conn.execute("""
        SELECT coalesce(i.error_code,q.error_code,m.error_code,'STAGE_EXECUTION_FAILED'),
               CASE WHEN i.id IS NULL THEN true ELSE coalesce(i.retryable,false) END
        FROM workflow_stage_attempt w
        LEFT JOIN ingestion_attempt i ON i.id=w.ingestion_attempt_id AND i.status='failed'
        LEFT JOIN quality_attempt q ON q.id=w.quality_attempt_id AND q.status='failed'
        LEFT JOIN matching_attempt m ON m.id=w.matching_attempt_id AND m.status='failed'
        WHERE w.workflow_id=%s AND w.step=%s AND w.status='running'
          AND num_nonnulls(i.id,q.id,m.id)=1
    """, (workflow_id, step)).fetchone()
    return row
