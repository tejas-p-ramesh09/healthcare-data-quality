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
        self.client = self.enterContext(TestClient(create_app(self.settings), base_url="http://localhost", headers=HEADERS))
        self.header = {"Idempotency-Key": uuid4().hex}

    def test_security_and_error_contract(self):
        with TestClient(create_app(self.settings), base_url="http://localhost") as anonymous:
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

    def test_wrong_result_link_and_failure_leave_prior_reports_visible(self):
        from psycopg.errors import CheckViolation
        from psycopg.types.json import Jsonb

        from healthcare_dq.workflow import service

        _, original = self.settings.catalog()
        tag = uuid4().hex

        def resolve(request):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += "-" + tag
            return assets

        with patch.object(Settings, "catalog", return_value=({}, resolve)):
            created = self.client.post("/v1/workflows", headers=self.header, json=REQUEST)
            wid, path = created.json()["id"], created.headers["location"]
            for _ in range(3):
                response = self.client.post(path + "/advance")
                self.assertEqual(response.status_code, 200, response.text)
            with restricted("workflow_executor") as conn:
                workflow, rows = service.read(conn, wid)
                with self.assertRaises(CheckViolation), conn.transaction():
                    expected = stages.key(workflow["spec"], 3, rows)
                    conn.execute("""UPDATE workflow_stage SET status='running',expected=%s
                        WHERE workflow_id=%s AND step=3""", (Jsonb(expected), wid))
                    conn.execute("""UPDATE workflow_stage SET status='completed',quality_id=%s
                        WHERE workflow_id=%s AND step=3""", (rows[2]["quality_id"], wid))
                self.assertNotEqual(rows[0]["batch_id"], rows[1]["batch_id"])
            self.assertEqual(self.client.post(path + "/advance").status_code, 200)
            with restricted("workflow_executor") as conn, self.assertRaises(CheckViolation):
                conn.execute("""UPDATE workflow_stage SET status='running',expected=%s
                        WHERE workflow_id=%s AND step=4""", (Jsonb(["wrong"]), wid))
            for _ in range(3):
                with patch.object(stages.matching, "rank", side_effect=ValueError("injected failure")):
                    result = self.client.post(path + "/advance")
                self.assertEqual(result.json()["stages"][4]["failure"]["code"], "MATCHING_FAILED")
            self.assertEqual(self.client.post(path + "/advance").json()["error"]["code"],
                             "STAGE_NOT_RETRYABLE")
            self.assertEqual(self.client.get(path + "/quality-findings?side=left").json()["items"], [])
            self.assertEqual(self.client.get(path + "/recommendations?method=exact").json()["error"]["code"],
                             "STAGE_FAILED")

    def test_pinned_identity_is_not_substituted(self):
        _, original = self.settings.catalog()
        tag = uuid4().hex

        def changed(request, replacement=False):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += "-" + tag
            if replacement:
                assets.spec["model_file_sha256"] = "0" * 64
            return assets

        with patch.object(Settings, "catalog", return_value=({}, changed)):
            created = self.client.post("/v1/workflows", headers=self.header, json=REQUEST)
        path = created.headers["location"]
        with patch.object(Settings, "catalog", return_value=({}, lambda request: changed(request, True))):
            self.assertEqual(self.client.post("/v1/workflows", headers=self.header, json=REQUEST).json()["id"],
                             created.json()["id"])
            self.assertEqual(self.client.post(path + "/advance").json()["error"]["code"], "ARTIFACT_UNAVAILABLE")
        self.assertEqual(self.client.get(path).json()["stages"][0]["status"], "pending")

    def test_database_roles_fail_closed(self):
        for reader, executor in (
            ("workflow_executor", "workflow_reporter"),
            ("workflow_executor", "workflow_executor"),
            (None, "workflow_executor"),
        ):
            with self.subTest(reader=reader, executor=executor):
                def report(reader=reader):
                    return restricted(reader) if reader else connect("TEST_DATABASE_URL")

                settings = Settings(TOKEN, reader=report, executor=lambda executor=executor: restricted(executor))
                with TestClient(create_app(settings), base_url="http://localhost", headers=HEADERS) as client:
                    for path in ("/health/ready", "/v1/workflows"):
                        result = client.get(path)
                        self.assertEqual(result.status_code, 503)
                        self.assertEqual(result.json()["error"]["code"], "SERVICE_NOT_READY")
                        self.assertNotIn("workflow_executor", result.text)

        from healthcare_dq.api.config import verify_connection

        for role, grant, revoke in (
            ("workflow_reporter", "GRANT SELECT(payload) ON raw_resource TO workflow_reporter",
             "REVOKE SELECT(payload) ON raw_resource FROM workflow_reporter"),
            ("workflow_reporter", "GRANT pg_read_all_data TO workflow_reporter",
             "REVOKE pg_read_all_data FROM workflow_reporter"),
            ("workflow_executor", "GRANT UPDATE(payload) ON raw_resource TO workflow_executor",
             "REVOKE UPDATE(payload) ON raw_resource FROM workflow_executor"),
        ):
            with connect("TEST_DATABASE_URL") as admin, admin.transaction():
                admin.execute(grant)
                admin.execute("SET LOCAL ROLE " + role)
                with self.assertRaisesRegex(ValueError, "Invalid database privileges"):
                    verify_connection(admin, role, injected=True)
                admin.execute("RESET ROLE")
                admin.execute(revoke)

    def test_workflow_attempt_journal_rejects_unauthorized_transitions(self):
        _, original = self.settings.catalog()

        def resolve(request):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += "-" + self.header["Idempotency-Key"]
            return assets

        with patch.object(Settings, "catalog", return_value=({}, resolve)):
            self.check_attempt_journal()

    def check_attempt_journal(self):
        from psycopg.errors import CheckViolation
        from psycopg.types.json import Jsonb

        from healthcare_dq.ingestion import ingest
        from healthcare_dq.workflow import service

        created = self.client.post("/v1/workflows", headers=self.header, json=REQUEST)
        wid, path = created.json()["id"], created.headers["location"]
        self.assertEqual(self.client.post(path + "/advance").status_code, 200)
        with restricted("workflow_executor") as runner:
            assets = self.settings.catalog()[1](REQUEST)
            right = ingest(runner, assets.spec["inputs"][1]["source"], assets.files[1])["batch_id"]
        with restricted("workflow_executor") as conn:
            workflow, rows = service.read(conn, wid)
            with self.assertRaises(CheckViolation):
                conn.execute("INSERT INTO workflow_stage_attempt(workflow_id,step) VALUES(%s,0)", (wid,))
            with self.assertRaises(CheckViolation):
                conn.execute("""UPDATE workflow_stage_attempt SET status='failed',code='fabricated',
                    retryable=false,finished_at=now() WHERE workflow_id=%s AND step=0""", (wid,))
            expected = Jsonb(stages.key(workflow["spec"], 1, rows))
            with self.assertRaises(CheckViolation), conn.transaction():
                conn.execute("UPDATE workflow_stage SET status='running',expected=%s WHERE workflow_id=%s AND step=1",
                             (expected, wid))
            with self.assertRaises(CheckViolation), conn.transaction():
                conn.execute("UPDATE workflow_stage SET status='running',expected=%s WHERE workflow_id=%s AND step=1",
                             (expected, wid))
                conn.execute("INSERT INTO workflow_stage_attempt(workflow_id,step) VALUES(%s,1)", (wid,))
                conn.execute("""UPDATE workflow_stage_attempt SET status='completed',finished_at=now()
                    WHERE workflow_id=%s AND step=1""", (wid,))
            with self.assertRaises(CheckViolation), conn.transaction():
                conn.execute("UPDATE workflow_stage SET status='running',expected=%s WHERE workflow_id=%s AND step=1",
                             (expected, wid))
                conn.execute("""UPDATE workflow_stage SET status='completed',batch_id=%s
                    WHERE workflow_id=%s AND step=1""", (right, wid))
        self.assertEqual(self.client.post(path + "/advance").status_code, 200)

    def test_guarded_advance_rejects_stale_and_malformed_headers(self):
        from healthcare_dq.workflow import service

        _, original = self.settings.catalog()
        tag = uuid4().hex

        def resolve(request):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += "-" + tag
            return assets

        with patch.object(Settings, "catalog", return_value=({}, resolve)):
            created = self.client.post("/v1/workflows", headers=self.header, json=REQUEST)
            path = created.headers["location"]
            for value in ("", "match:pending:01", "fake:pending:0", "match:completed:0",
                          "match:failed:-1", "match:failed:9223372036854775808"):
                rejected = self.client.post(path + "/advance", headers={"X-Expected-Stage": value})
                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(rejected.json()["error"]["code"], "INVALID_EXPECTED_STAGE")
            headers = {"X-Expected-Stage": "ingest_left:pending:0"}
            with ThreadPoolExecutor() as pool:
                def advance(_):
                    return self.client.post(path + "/advance", headers=headers)

                responses = list(pool.map(advance, range(2)))
            self.assertEqual(sum(r.status_code == 200 for r in responses), 1)
            self.assertIn(next(r for r in responses if r.status_code != 200).json()["error"]["code"],
                          {"STAGE_STATE_CHANGED", "WORKFLOW_BUSY", "EXECUTION_CAPACITY_BUSY"})
            rejected = self.client.post(path + "/advance", headers=headers)
            self.assertEqual(rejected.json()["error"]["code"], "STAGE_STATE_CHANGED")
            with restricted("workflow_executor") as conn:
                _, rows = service.read(conn, created.json()["id"])
                self.assertEqual([row["attempt_count"] for row in rows], [1, 0, 0, 0, 0])
            legacy = self.client.post(path + "/advance")
            self.assertEqual(legacy.json()["stages"][1]["status"], "completed")

    def test_workflow_listing_traverses_multiple_scope_bound_pages(self):
        _, original = self.settings.catalog()
        tag = uuid4().hex
        resolved = []

        def resolve(request):
            assets = original(request)
            for side in assets.spec["inputs"]:
                side["source"] += f"-{tag}-{len(resolved)}"
            return assets

        with patch.object(Settings, "catalog", return_value=({}, resolve)):
            for _ in range(3):
                result = self.client.post("/v1/workflows", headers={"Idempotency-Key": uuid4().hex}, json=REQUEST)
                self.assertEqual(result.status_code, 201)
                resolved.append(result.json()["id"])

        cursor = None
        traversed = []
        while True:
            query = {"limit": 1}
            if cursor:
                query["cursor"] = cursor
            response = self.client.get("/v1/workflows", params=query)
            self.assertEqual(response.status_code, 200)
            contents = response.json()
            traversed.extend(row["id"] for row in contents["items"])
            self.assertEqual(len(traversed), len(set(traversed)))
            cursor = contents["next_cursor"]
            if not cursor:
                break
        self.assertEqual([wid for wid in traversed if wid in resolved], resolved)
