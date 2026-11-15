import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.errors import InsufficientPrivilege

from healthcare_dq.api.app import create_app
from healthcare_dq.api.config import Settings
from healthcare_dq.db import connect, migrate
from healthcare_dq.workflow import stages

TOKEN = "test-token-only-for-local-http-fixtures"
HEADERS = {"Authorization": "Bearer " + TOKEN}
REQUEST = {"left_fixture": "demo-source-a", "right_fixture": "demo-source-b", "profile": "demo-v1"}


def restricted(role):
    c = connect("TEST_DATABASE_URL")
    c.execute("SET ROLE " + role)  # Callers pass a fixed role constant.
    return c


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class WorkflowAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connect("TEST_DATABASE_URL") as c:
            migrate(c)

    def setUp(self):
        self.settings = Settings(TOKEN, reader=lambda: restricted("workflow_reporter"),
                                 executor=lambda: restricted("workflow_executor"))
        self.client = self.enterContext(TestClient(create_app(self.settings), headers=HEADERS))
        self.header = {"Idempotency-Key": uuid4().hex}

    def test_security_and_error_contract(self):
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.get("/v1/catalog").status_code, 401)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertEqual(self.client.post("/v1/workflows", content="x").status_code, 415)
        self.assertEqual(self.client.post("/v1/workflows", headers={"content-type": "application/json"},
                                         content=b" " * 16385).status_code, 413)
        for body, expected in (
            ({**REQUEST, "path": "/do/not/echo"}, "INVALID_REQUEST"),
            ({**REQUEST, "left_fixture": "unknown"}, "UNKNOWN_FIXTURE"),
            ({**REQUEST, "right_fixture": REQUEST["left_fixture"]}, "SAME_SOURCE_NOT_SUPPORTED"),
        ):
            result = self.client.post("/v1/workflows", headers=self.header, json=body)
            self.assertEqual(result.json()["error"]["code"], expected)
            self.assertNotIn("/do/not/echo", result.text)
        result = self.client.get("/v1/workflows/2147483647/quality-findings?side=left")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.json()["error"]["code"], "WORKFLOW_NOT_FOUND")
        with restricted("workflow_reporter") as c:
            with self.assertRaises(InsufficientPrivilege):
                c.execute("SELECT payload FROM raw_resource")
            with self.assertRaises(InsufficientPrivilege):
                c.execute("UPDATE workflow_stage SET status='completed'")
        with restricted("workflow_executor") as c, self.assertRaises(InsufficientPrivilege):
            c.execute("CREATE TABLE private_workflow_test(id int)")

    def test_demo_replay_and_reports(self):
        url = "/v1/workflows"
        first = self.client.post(url, headers=self.header, json=REQUEST)
        self.assertIn(first.status_code, (200, 201), first.text)
        path = first.headers["location"]
        again = self.client.post(url, headers=self.header, json=REQUEST)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.json()["id"], again.json()["id"])
        conflict = self.client.post(url, headers=self.header, json={
            **REQUEST, "left_fixture": REQUEST["right_fixture"], "right_fixture": REQUEST["left_fixture"],
        })
        self.assertEqual(conflict.json()["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")
        if first.json()["status"] == "pending":
            pending = self.client.get(path + "/quality-findings?side=left")
            self.assertEqual(pending.json()["error"]["code"], "STAGE_NOT_READY")
        for _ in range(5):
            result = self.client.post(path + "/advance")
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "completed")
        self.assertEqual(result.json()["stages"][-1]["counts"]["no_candidate_right"], 1)
        for side, count in (("left", 0), ("right", 1)):
            report = self.client.get(path + f"/quality-findings?side={side}")
            self.assertEqual(len(report.json()["items"]), count)
            sources = self.client.get(path + f"/batches/{side}/files").json()["items"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(len(sources[0]["sha256"]), 64)
            if count:
                self.assertEqual(report.json()["items"][0]["file_sha256"], sources[0]["sha256"])
        for method in ("exact", "statistical"):
            report = self.client.get(path + f"/recommendations?method={method}").json()
            [pair] = report["items"]
            self.assertEqual(pair["disposition"], "suggested_match")
            self.assertEqual(pair["evidence"]["levels"]["given"], "exact")
            self.assertNotIn("1980-01-01", str(report))
        replay = self.client.post(path + "/advance").json()
        self.assertEqual(replay["stages"], result.json()["stages"])

    def test_concurrent_submission_and_advancement(self):
        _, original = self.settings.catalog()
        suffix = uuid4().hex

        def resolve(request):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += "-" + suffix
            return assets

        with patch.object(Settings, "catalog", return_value=({}, resolve)), ThreadPoolExecutor() as pool:
            def submit(key):
                return self.client.post("/v1/workflows", headers={"Idempotency-Key": key}, json=REQUEST)

            results = list(pool.map(submit, (suffix, suffix, uuid4().hex)))
            self.assertEqual(sorted(r.status_code for r in results), [200, 200, 201])
            self.assertEqual(len({r.json()["id"] for r in results}), 1)
            path = results[0].headers["location"]
            entered, release = Event(), Event()
            original_execute = stages.execute

            def held(*args):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
                return original_execute(*args)

            with patch.object(stages, "execute", side_effect=held):
                pending = pool.submit(self.client.post, path + "/advance")
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(self.client.get(path).json()["status"], "running")
                    busy = self.client.post(path + "/advance")
                    self.assertEqual(busy.json()["error"]["code"], "EXECUTION_CAPACITY_BUSY")
                finally:
                    release.set()
                self.assertEqual(pending.result(5).json()["stages"][0]["status"], "completed")
            other = self.client.post("/v1/workflows", headers={"Idempotency-Key": uuid4().hex}, json={
                **REQUEST, "left_fixture": REQUEST["right_fixture"], "right_fixture": REQUEST["left_fixture"],
            })
            self.assertEqual(other.status_code, 201)
            listing = self.client.get("/v1/workflows?limit=1").json()
            self.assertTrue(listing["next_cursor"])
            nxt = self.client.get("/v1/workflows", params={"limit": 1, "cursor": listing["next_cursor"]})
            self.assertEqual(nxt.status_code, 200)
            self.assertNotEqual(listing["items"][0]["id"], nxt.json()["items"][0]["id"])
            bad = self.client.get(path + "/batches/left/files", params={"cursor": listing["next_cursor"]})
            self.assertEqual(bad.json()["error"]["code"], "INVALID_CURSOR")
