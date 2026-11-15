import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from healthcare_dq.db import connect, migrate
from healthcare_dq.workflow import service, stages
from healthcare_dq.workflow.catalog import load_catalog

REQUEST = {"left_fixture": "demo-source-a", "right_fixture": "demo-source-b", "profile": "demo-v1"}


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class WorkflowTests(unittest.TestCase):
    def test_reconcile_before_and_after_stage_commit(self):
        _, resolve = load_catalog()

        def connection():
            return connect("TEST_DATABASE_URL")

        with connection() as c:
            migrate(c)
            # Find a fresh identity without modifying the approved fixture bytes.
            original = resolve

            def resolve(request):
                assets = original(request)
                for row in assets.spec["inputs"]:
                    row["source"] += f"-test-{tag}"
                return assets

            tag = uuid4().hex
            wid, created = service.register(c, REQUEST, str(uuid4()), resolve)
            self.assertTrue(created)
            with patch.object(stages, "execute", side_effect=ConnectionError("before commit")), \
                    self.assertRaises(ConnectionError):
                service.advance(wid, resolve, connection)
            with patch.object(service, "after_execute", side_effect=ConnectionError("after commit")), \
                    self.assertRaises(ConnectionError):
                service.advance(wid, resolve, connection)
            _, progress = service.read(c, wid)
            self.assertEqual(progress[0]["status"], "running")
            committed = stages.lookup(c, 0, progress[0]["expected"])
            self.assertIsNotNone(committed)
            with patch.object(stages, "execute", side_effect=AssertionError("already committed")):
                service.advance(wid, resolve, connection)
            _, progress = service.read(c, wid)
            self.assertEqual(progress[0]["batch_id"], committed)
            self.assertEqual(c.execute("""SELECT status FROM workflow_stage_attempt
                WHERE workflow_id=%s AND step=0 ORDER BY id""", (wid,)).fetchall(),
                             [("interrupted",), ("completed",)])
