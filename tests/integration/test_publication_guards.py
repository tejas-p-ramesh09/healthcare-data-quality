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
