"""PostgreSQL queries for immutable quality execution results."""

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .engine import Record, digest


def register(conn, batch_id, config, revision):
    key = digest(config)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM ingestion_batch WHERE id=%s AND status='published'",
                        (batch_id,)).fetchone() is None:
            raise ValueError("PUBLISHED_BATCH_REQUIRED")
        conn.execute("""
            INSERT INTO quality_run(batch_id,ruleset_sha256,ruleset,engine_revision)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
        """, (batch_id, key, Jsonb(config), revision))
        return conn.execute("""
            SELECT id FROM quality_run WHERE batch_id=%s AND ruleset_sha256=%s AND engine_revision=%s
        """, (batch_id, key, revision)).fetchone()[0]


def records(conn, batch_id):
    return [Record(*row) for row in conn.execute("""
        SELECT r.path,r.entry_index,r.resource_type,r.fhir_id,r.payload,o.encounter_id
        FROM raw_resource r LEFT JOIN observation o
          ON (o.batch_id,o.path,o.entry_index)=(r.batch_id,r.path,r.entry_index)
        WHERE r.batch_id=%s AND r.disposition='normalized' ORDER BY r.path,r.entry_index
    """, (batch_id,))]


def status(conn, run_id):
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute("SELECT * FROM quality_run WHERE id=%s", (run_id,)).fetchone()
        if row is None:
            raise ValueError("UNKNOWN_QUALITY_RUN")
        row["attempts"] = cur.execute(
            "SELECT * FROM quality_attempt WHERE run_id=%s ORDER BY id", (run_id,),
        ).fetchall()
        return row


def findings(conn, run_id):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("""
            SELECT q.*,f.sha256 AS file_sha256,r.resource_type,r.fhir_id,
                   b.manifest_sha256,s.source_key
            FROM quality_finding q JOIN quality_run v ON v.id=q.run_id
            JOIN ingestion_file f ON (f.batch_id,f.path)=(q.batch_id,q.path)
            JOIN raw_resource r ON (r.batch_id,r.path,r.entry_index)=(q.batch_id,q.path,q.entry_index)
            JOIN ingestion_batch b ON b.id=q.batch_id
            JOIN ingestion_source s ON s.id=b.source_id
            WHERE q.run_id=%s AND v.status='completed' ORDER BY q.path,q.entry_index,q.rule_id,q.pointer
        """, (run_id,)).fetchall()


def publish(conn, run_id, batch_id, rows):
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO quality_finding VALUES (%(run_id)s,%(batch_id)s,%(path)s,%(entry_index)s,
                %(rule_id)s,%(rule_version)s,%(category)s,%(severity)s,%(pointer)s,%(diagnostic)s,
                %(related_path)s,%(related_index)s)
        """, [dict(run_id=run_id, batch_id=batch_id, **row) for row in rows])
