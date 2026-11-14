import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg

from healthcare_dq import ingestion
from healthcare_dq.db import connect, migrate
from healthcare_dq.inputs import InputError, InputFile, discover
from tests.unit.test_ingestion_inputs import entry, file, ref


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL integration")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect("TEST_DATABASE_URL") as conn:
            migrate(conn)
            migrate(conn)

    def setUp(self):
        self.source = f"test-{uuid4()}"
        self.files = [file(
            entry("Patient", "a"), entry("Patient", "b"),
            entry("Encounter", "e", status="finished", subject=ref("Patient/a")),
            entry("Observation", "o", status="final", code={}, subject=ref("Patient/a"),
                  encounter=ref("Encounter/e")),
            entry("Condition", "c", subject=ref("Patient/a")),
            entry("MedicationRequest", "m", status="active", intent="order", subject=ref("Patient/a")),
            entry("Organization", "g"),
        )]

    def submit(self, **kwargs):
        with connect("TEST_DATABASE_URL") as conn:
            return ingestion.ingest(conn, self.source, self.files, **kwargs)

    def counts(self, batch_id):
        with connect("TEST_DATABASE_URL") as conn:
            return tuple(conn.execute(f"SELECT count(*) FROM {table} WHERE batch_id=%s",
                                      (batch_id,)).fetchone()[0]
                         for table in ("raw_resource", "patient", "encounter", "observation",
                                       "condition", "medication_request"))

    def test_snapshot_replay_provenance_and_database_constraints(self):
        result = self.submit()
        batch = result["batch_id"]
        self.assertEqual(result["counts"]["raw_only"], 1)
        self.assertEqual(self.counts(batch), (7, 2, 1, 1, 1, 1))
        self.assertTrue(self.submit(retry_batch=batch)["replayed"])
        self.assertEqual(self.counts(batch), (7, 2, 1, 1, 1, 1))
        with connect("TEST_DATABASE_URL") as conn:
            row = conn.execute("""
                SELECT r.fhir_id, f.sha256, s.source_key FROM observation n
                JOIN raw_resource r USING (batch_id,path,entry_index)
                JOIN ingestion_file f USING (batch_id,path)
                JOIN ingestion_batch b ON b.id=n.batch_id
                JOIN ingestion_source s ON s.id=b.source_id WHERE n.batch_id=%s
            """, (batch,)).fetchone()
            self.assertEqual(row, ("o", self.files[0].sha256, self.source))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute("UPDATE observation SET patient_id='b' WHERE batch_id=%s", (batch,))
            other = ingestion.ingest(conn, self.source + "-other", self.files)
            self.assertNotEqual(other["batch_id"], batch)
        self.files = [file(entry("Patient", "new"))]
        with self.assertRaisesRegex(InputError, "RETRY_MANIFEST_MISMATCH"):
            self.submit(retry_batch=batch)
        self.assertNotEqual(self.submit()["batch_id"], batch)

    def test_invalid_json_and_unresolved_link_are_durable_and_sanitized(self):
        self.files = [InputFile("invalid.json", b'{"name":"Secret Fixture","birthDate":"1980-01-01"',
                                "0" * 64)]
        result = self.submit()
        self.assertEqual(result["attempts"][0]["code"], "INVALID_JSON")
        self.assertNotIn("Secret Fixture", str(result))
        self.assertNotIn("1980-01-01", str(result))
        self.assertEqual(self.counts(result["batch_id"]), (0,) * 6)
        self.files = [file(entry("Condition", "x", subject=ref("Patient/missing")))]
        result = self.submit()
        self.assertEqual(result["attempts"][0]["code"], "UNRESOLVED_REFERENCE")
        self.assertEqual(self.counts(result["batch_id"]), (0,) * 6)

    def test_mid_write_constraint_failure_rolls_back_and_can_retry(self):
        original = ingestion.publish

        def fail_after_writes(conn, batch, rows):
            original(conn, batch, rows)
            conn.execute("INSERT INTO patient (batch_id,fhir_id,path,entry_index) VALUES (%s,'bad','x',0)",
                         (batch,))

        with patch.object(ingestion, "publish", side_effect=fail_after_writes):
            result = self.submit()
        self.assertEqual(result["attempts"][0]["code"], "PUBLICATION_FAILED")
        self.assertEqual(self.counts(result["batch_id"]), (0,) * 6)
        self.assertEqual(self.submit(retry_batch=result["batch_id"])["status"], "published")
        self.assertEqual(self.counts(result["batch_id"]), (7, 2, 1, 1, 1, 1))

    def test_two_connections_contend_without_duplicate_publication(self):
        entered, release = Event(), Event()
        original = ingestion.prepare

        def wait_before_publish(files):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release worker")
            return original(files)

        with patch.object(ingestion, "prepare", side_effect=wait_before_publish), ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(self.submit)
            try:
                self.assertTrue(entered.wait(10))
                second = self.submit()
                self.assertEqual(second["status"], "busy")
            finally:
                release.set()
            self.assertEqual(first.result(10)["batch_id"], second["batch_id"])
        self.assertTrue(self.submit()["replayed"])
        self.assertEqual(self.counts(second["batch_id"]), (7, 2, 1, 1, 1, 1))

    def test_session_loss_rolls_back_and_retry_marks_interrupted(self):
        original = ingestion.publish

        def lose_connection(conn, batch, rows):
            original(conn, batch, rows)
            pid = conn.info.backend_pid
            with connect("TEST_DATABASE_URL") as other:
                self.assertTrue(other.execute("SELECT pg_terminate_backend(%s,5000)", (pid,)).fetchone()[0])
            conn.execute("SELECT 1")

        with patch.object(ingestion, "publish", side_effect=lose_connection), self.assertRaises(psycopg.OperationalError):
            self.submit()
        with connect("TEST_DATABASE_URL") as conn:
            batch, = conn.execute("""
                SELECT b.id FROM ingestion_batch b JOIN ingestion_source s ON s.id=b.source_id
                WHERE source_key=%s
            """, (self.source,)).fetchone()
        self.assertEqual(self.counts(batch), (0,) * 6)
        final = self.submit(retry_batch=batch)
        self.assertEqual([a["status"] for a in final["attempts"]], ["interrupted", "published"])

    def test_lost_success_response_returns_the_same_committed_result(self):
        original = ingestion.status

        def lost_response(conn, batch_id):
            result = original(conn, batch_id)
            if result["status"] == "published":
                raise psycopg.OperationalError("simulated lost commit response")
            return result

        with patch.object(ingestion, "status", side_effect=lost_response), self.assertRaises(psycopg.OperationalError):
            self.submit()
        result = self.submit()
        self.assertTrue(result["replayed"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(self.counts(result["batch_id"]), (7, 2, 1, 1, 1, 1))

    @unittest.skipUnless(os.getenv("SYNTHEA_BASELINE"), "Retained Synthea baseline is local")
    def test_retained_baseline(self):
        self.files = discover(Path(os.environ["SYNTHEA_BASELINE"]))
        result = self.submit()
        self.assertEqual(self.counts(result["batch_id"]), (3099, 5, 197, 1196, 154, 126))
        self.assertEqual(result["counts"]["normalized"], 1678)
        self.assertTrue(self.submit()["replayed"])
