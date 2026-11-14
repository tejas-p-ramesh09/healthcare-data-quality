import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg

from healthcare_dq.db import connect, migrate
from healthcare_dq.ingestion import ingest
from healthcare_dq.matching import service
from healthcare_dq.matching.core import compare, features, fit
from tests.unit.test_ingestion_inputs import entry, file


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class MatchingRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect("TEST_DATABASE_URL") as c:
            migrate(c)

    def setUp(self):
        attrs = {"birthDate": "2000-01-01", "name": [{"given": ["Synthetic"], "family": "Fixture"}]}
        self.ids = []
        with connect("TEST_DATABASE_URL") as c:
            for name in ("a", "b"):
                self.ids.append(ingest(c, str(uuid4()), [file(entry("Patient", name, **attrs))])["batch_id"])
        x = features(attrs)
        self.model = {"statistical": fit([(compare(x, x), True), (compare(x, features({})), False)]),
                      "policies": {"exact": {"low": 0, "high": 1, "margin": 0},
                                   "statistical": {"low": 0, "high": 1, "margin": 0}}}

    def submit(self):
        with connect("TEST_DATABASE_URL") as c:
            c.execute("SET ROLE ingest")
            return service.run(c, *self.ids, self.model)

    def test_provenance_replay_and_completed_run_permissions(self):
        result = self.submit()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(self.submit()["replayed"])
        rid = result["id"]
        with connect("TEST_DATABASE_URL") as c:
            c.execute("SET ROLE ingest")
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute("INSERT INTO matching_candidate VALUES (%s,%s,%s,'a','b','[]')", (rid, *self.ids))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("UPDATE matching_score SET score=0 WHERE run_id=%s", (rid,))
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute("UPDATE matching_run SET status='running' WHERE id=%s", (rid,))

    def test_invalid_endpoint_and_rollback(self):
        original = service.publish

        def fault(c, rid, left, right, rows, decisions):
            original(c, rid, left, right, rows, decisions)
            c.execute("INSERT INTO matching_candidate VALUES (%s,%s,%s,'invalid','b','[]')",
                      (rid, left, right))

        with patch.object(service, "publish", side_effect=fault):
            attempt = self.submit()
        self.assertEqual(attempt["status"], "failed")
        with connect("TEST_DATABASE_URL") as c:
            for table in ("matching_candidate", "matching_score", "matching_recommendation"):
                self.assertEqual(c.execute(f"SELECT count(*) FROM {table} WHERE run_id=%s",
                                           (attempt["id"],)).fetchone(), (0,))
        self.assertEqual(self.submit()["id"], attempt["id"])

    def test_connection_loss_and_uncertain_response(self):
        original = service.publish

        def kill(c, *args):
            original(c, *args)
            with connect("TEST_DATABASE_URL") as other:
                other.execute("SELECT pg_terminate_backend(%s,5000)", (c.info.backend_pid,))
            c.execute("SELECT 1")

        with patch.object(service, "publish", side_effect=kill), self.assertRaises(psycopg.OperationalError):
            self.submit()
        with connect("TEST_DATABASE_URL") as c:
            self.assertEqual(c.execute("""
                SELECT count(*) FROM matching_candidate a JOIN matching_run r ON a.run_id=r.id
                WHERE r.left_batch=%s AND r.right_batch=%s
            """, self.ids).fetchone(), (0,))
        status = service.status

        def lost(c, rid):
            result = status(c, rid)
            if result["status"] == "completed":
                raise psycopg.OperationalError
            return result

        with patch.object(service, "status", side_effect=lost), self.assertRaises(psycopg.OperationalError):
            self.submit()
        result = self.submit()
        self.assertTrue(result["replayed"])
        self.assertEqual([x[1] for x in result["attempts"]], ["interrupted", "completed"])

    def test_concurrent_same_key(self):
        entered, release = Event(), Event()
        original = service.rank

        def pause(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError
            return original(*args, **kwargs)

        with patch.object(service, "rank", side_effect=pause), ThreadPoolExecutor() as pool:
            first = pool.submit(self.submit)
            try:
                self.assertTrue(entered.wait(10))
                busy = self.submit()
                self.assertEqual(busy["status"], "busy")
            finally:
                release.set()
            self.assertEqual(first.result(10)["id"], busy["id"])
        self.assertEqual(len(self.submit()["attempts"]), 1)
