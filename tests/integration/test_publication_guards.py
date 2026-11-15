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

    def test_upgrade_preserves_phase3_snapshots(self):
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
            for p in sorted((ROOT / "migrations").glob("*.sql"))[:6]:
                (root / "migrations" / p.name).write_bytes(p.read_bytes())
            with patch("healthcare_dq.db.ROOT", root):
                migrate(conn)
            batch = ingest(conn, "upgrade-fixture", [file(entry("Patient", "a"))])
            run = validate(conn, batch["batch_id"], ruleset(ROOT / "config/quality/ruleset-v1.json"))
            migrate(conn)
            migrate(conn)
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migration").fetchone(), (9,))
            self.assertEqual(conn.execute("SELECT counts FROM ingestion_batch WHERE id=%s",
                                          (batch["batch_id"],)).fetchone()[0], batch["counts"])
            self.assertEqual(conn.execute("SELECT counts FROM quality_run WHERE id=%s",
                                          (run["id"],)).fetchone()[0], run["counts"])
