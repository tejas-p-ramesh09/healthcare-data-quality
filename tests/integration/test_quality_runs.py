import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg

from healthcare_dq.db import ROOT, connect, migrate
from healthcare_dq.ingestion import ingest
from healthcare_dq.quality import repository as repo
from healthcare_dq.quality import service
from healthcare_dq.quality.engine import ruleset
from tests.unit.test_ingestion_inputs import entry, file


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class QualityRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect("TEST_DATABASE_URL") as conn:
            migrate(conn)

    def setUp(self):
        with connect("TEST_DATABASE_URL") as conn:
            self.batch = ingest(conn, str(uuid4()), [file(entry("Patient", "a"))])["batch_id"]
        self.config = ruleset(ROOT / "config/quality/ruleset-v1.json")

    def submit(self):
        with connect("TEST_DATABASE_URL") as conn:
            return service.validate(conn, self.batch, self.config)

    def test_replay_provenance_and_config_identity(self):
        first = self.submit()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(self.submit()["id"], first["id"])
        self.assertTrue(self.submit()["replayed"])
        with connect("TEST_DATABASE_URL") as conn:
            [finding] = repo.findings(conn, first["id"])
            self.assertEqual((finding["batch_id"], finding["fhir_id"], finding["rule_version"]),
                             (self.batch, "a", 1))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute("UPDATE quality_finding SET batch_id=-1 WHERE run_id=%s", (first["id"],))
        self.config["encounter_grace_hours"] = 3
        self.assertNotEqual(self.submit()["id"], first["id"])

    def test_concurrent_run(self):
        entered, release = Event(), Event()
        original = service.evaluate

        def pause(*args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError
            return original(*args)

        with patch.object(service, "evaluate", side_effect=pause), ThreadPoolExecutor() as pool:
            future = pool.submit(self.submit)
            try:
                self.assertTrue(entered.wait(10))
                busy = self.submit()
                self.assertEqual(busy["status"], "busy")
            finally:
                release.set()
            self.assertEqual(future.result(10)["id"], busy["id"])
        self.assertEqual(len(self.submit()["attempts"]), 1)

    def test_rollback_and_interruption(self):
        original = repo.publish

        def fault(conn, run_id, batch_id, rows):
            original(conn, run_id, batch_id, rows)
            if kill:
                with connect("TEST_DATABASE_URL") as other:
                    other.execute("SELECT pg_terminate_backend(%s,5000)", (conn.info.backend_pid,))
                conn.execute("SELECT 1")
            else:
                original(conn, run_id, batch_id, rows)

        for kill in (False, True):
            self.config["encounter_grace_hours"] += 1
            with patch.object(repo, "publish", side_effect=fault):
                if kill:
                    with self.assertRaises(psycopg.OperationalError):
                        self.submit()
                else:
                    self.assertEqual(self.submit()["status"], "failed")
            with connect("TEST_DATABASE_URL") as conn:
                rid, = conn.execute("SELECT max(id) FROM quality_run WHERE batch_id=%s",
                                    (self.batch,)).fetchone()
                self.assertEqual(conn.execute("SELECT count(*) FROM quality_finding WHERE run_id=%s",
                                              (rid,)).fetchone(), (0,))
            final = self.submit()
            self.assertEqual(final["id"], rid)
            self.assertEqual([a["status"] for a in final["attempts"]],
                             ["interrupted" if kill else "failed", "completed"])

    def test_unexpected_rule_error_and_lost_commit_response(self):
        with patch.object(service, "evaluate", side_effect=RuntimeError("secret fixture")):
            failed = self.submit()
        self.assertEqual(failed["attempts"][0]["error_code"], "VALIDATION_ENGINE_FAILED")
        original = repo.status

        def lose_response(conn, run_id):
            result = original(conn, run_id)
            if result["status"] == "completed":
                raise psycopg.OperationalError("lost response")
            return result

        with patch.object(repo, "status", side_effect=lose_response), self.assertRaises(psycopg.OperationalError):
            self.submit()
        result = self.submit()
        self.assertTrue(result["replayed"])
        self.assertEqual([a["status"] for a in result["attempts"]], ["failed", "completed"])
