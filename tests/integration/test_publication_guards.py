import os
import unittest
from uuid import uuid4

from psycopg.errors import CheckViolation, InsufficientPrivilege

from healthcare_dq.db import ROOT, connect, migrate
from healthcare_dq.ingestion import ingest
from healthcare_dq.quality.engine import ruleset
from healthcare_dq.quality.service import validate
from tests.unit.test_ingestion_inputs import entry, file


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class PublicationGuards(unittest.TestCase):
    def test_runtime_registration_and_late_writes(self):
        with connect("TEST_DATABASE_URL") as conn:
            migrate(conn)
            conn.execute("SET ROLE ingest")
            batch = ingest(conn, str(uuid4()), [file(entry("Patient", "a"))])["batch_id"]
            run = validate(conn, batch, ruleset(ROOT / "config/quality/ruleset-v1.json"))["id"]
            with self.assertRaises(InsufficientPrivilege):
                conn.execute("INSERT INTO ingestion_batch(status) VALUES ('published')")
            with self.assertRaises(InsufficientPrivilege):
                conn.execute("INSERT INTO quality_run(status) VALUES ('completed')")
            with self.assertRaises(CheckViolation):
                conn.execute("UPDATE ingestion_batch SET status='running' WHERE id=%s", (batch,))
            with self.assertRaises(CheckViolation):
                conn.execute("UPDATE quality_run SET status='running' WHERE id=%s", (run,))
            with self.assertRaises(CheckViolation):
                conn.execute("INSERT INTO ingestion_file VALUES (%s,'late',0,'hash')", (batch,))
            with self.assertRaises(CheckViolation):
                conn.execute("""INSERT INTO quality_finding
                    SELECT run_id,batch_id,path,entry_index,rule_id,rule_version,
                           category,severity,'/late',diagnostic,related_path,related_index
                    FROM quality_finding WHERE run_id=%s""", (run,))

    def test_upgrade_preserves_phase3_and_phase4_snapshots(self):
        for version in (6, 9):
            with self.subTest(version=version):
                self.check_upgrade(version)

    def check_upgrade(self, version):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from psycopg import connect as pg_connect
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        name = "upgrade_" + uuid4().hex
        dsn = make_conninfo(os.environ["TEST_DATABASE_URL"], dbname=name)
        with connect("TEST_DATABASE_URL") as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        with pg_connect(dsn, autocommit=True) as conn, TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "migrations").mkdir()
            for p in sorted((ROOT / "migrations").glob("*.sql"))[:version]:
                (root / "migrations" / p.name).write_bytes(p.read_bytes())
            with patch("healthcare_dq.db.ROOT", root):
                migrate(conn)
            batch = ingest(conn, "upgrade-fixture", [file(entry("Patient", "a"))])
            run = validate(conn, batch["batch_id"], ruleset(ROOT / "config/quality/ruleset-v1.json"))
            if version == 9:
                from healthcare_dq.workflow import service
                from healthcare_dq.workflow.catalog import load_catalog

                _, resolve = load_catalog()
                wid, _ = service.register(conn, {
                    "left_fixture": "demo-source-a", "right_fixture": "demo-source-b", "profile": "demo-v1",
                }, "upgrade-test", resolve)
                conn.execute("""UPDATE workflow_stage SET status='running',expected=
                    jsonb_build_array(spec->'inputs'->0->'source',spec->'inputs'->0->'manifest',
                                      spec->'ingestion_contract')
                    FROM workflow WHERE workflow_stage.workflow_id=workflow.id AND workflow_id=%s AND step=0
                """, (wid,))
                conn.execute("INSERT INTO workflow_stage_attempt(workflow_id,step) VALUES (%s,0)", (wid,))
                history = conn.execute("SELECT id,status FROM workflow_stage_attempt").fetchall()
            migrate(conn)
            migrate(conn)
            if version == 9:
                self.assertEqual(conn.execute("SELECT id,status FROM workflow_stage_attempt").fetchall(), history)
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migration").fetchone(), (11,))
            self.assertEqual(conn.execute("SELECT counts FROM ingestion_batch WHERE id=%s",
                                          (batch["batch_id"],)).fetchone()[0], batch["counts"])
            self.assertEqual(conn.execute("SELECT counts FROM quality_run WHERE id=%s",
                                          (run["id"],)).fetchone()[0], run["counts"])

    def test_workflow_executor_cannot_bypass_publication_contract(self):
        from psycopg.types.json import Jsonb

        from healthcare_dq.quality.engine import digest

        with connect("TEST_DATABASE_URL") as conn:
            from healthcare_dq.ingestion import register

            migrate(conn)
            conn.execute("SET ROLE workflow_executor")
            source = str(uuid4())
            fixture = file(entry("Patient", "a", birthDate="2000-01-01"), path='é-😀-".json')
            config = ruleset(ROOT / "config/quality/ruleset-v1.json")
            batch = register(conn, source, [fixture])
            with self.assertRaises(CheckViolation):
                conn.execute("""INSERT INTO quality_run(batch_id,ruleset_sha256,ruleset,engine_revision)
                    VALUES(%s,%s,%s,'test')""", (batch, digest(config), Jsonb(config)))
            for provenance in ([], [("wrong.json", 1, "0" * 64)]):
                with self.assertRaises(CheckViolation), conn.transaction():
                    false_batch, = conn.execute("""
                        INSERT INTO ingestion_batch(source_id,manifest_sha256,contract_version)
                        SELECT id,%s,1 FROM ingestion_source WHERE source_key=%s RETURNING id
                    """, ("a" * 64, source)).fetchone()
                    for row in provenance:
                        conn.execute("INSERT INTO ingestion_file VALUES(%s,%s,%s,%s)", (false_batch, *row))
                    conn.execute("UPDATE ingestion_batch SET status='running' WHERE id=%s", (false_batch,))

        # Underlying stage sessions own their locks, including replay.
        def call(function, *args):
            with connect("TEST_DATABASE_URL") as runner:
                runner.execute("SET ROLE workflow_executor")
                return function(runner, *args)

        published = call(ingest, source, [fixture])
        self.assertEqual(published["status"], "published")
        self.assertTrue(call(ingest, source, [fixture])["replayed"])
        completed = call(validate, batch, config)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(call(validate, batch, config)["replayed"])
        self.assertTrue(all(row["finding"] == 0 for row in completed["counts"].values()))

        with connect("TEST_DATABASE_URL") as conn:
            conn.execute("SET ROLE workflow_executor")
            for invalid in ({}, {**config, "encounter_grace_hours": "24"}):
                with self.assertRaises(CheckViolation):
                    conn.execute("""INSERT INTO quality_run(batch_id,ruleset_sha256,ruleset,engine_revision)
                        VALUES(%s,%s,%s,'test')""", (batch, digest(invalid), Jsonb(invalid)))
            with self.assertRaises(CheckViolation), conn.transaction():
                rid, = conn.execute("""INSERT INTO quality_run(batch_id,ruleset_sha256,ruleset,engine_revision)
                    VALUES(%s,%s,%s,'test') RETURNING id""", (batch, digest(config), Jsonb(config))).fetchone()
                conn.execute("UPDATE quality_run SET status='running' WHERE id=%s", (rid,))
                aid, = conn.execute("INSERT INTO quality_attempt(run_id) VALUES(%s) RETURNING id", (rid,)).fetchone()
                conn.execute("UPDATE quality_attempt SET status='completed',finished_at=now() WHERE id=%s", (aid,))
                counts = {key: {**value, "pass_": 0} for key, value in completed["counts"].items()}
                conn.execute("UPDATE quality_run SET status='completed',counts=%s,completed_at=now() WHERE id=%s",
                             (Jsonb(counts), rid))
