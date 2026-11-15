import base64
import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from healthcare_dq.api.reports import decode
from healthcare_dq.workflow import stages
from healthcare_dq.workflow.catalog import safe
from healthcare_dq.workflow.service import WorkflowError


class WorkflowContract(unittest.TestCase):
    def test_cursor_is_typed_and_scoped_to_resource_filters(self):
        def cursor(scope, value):
            return base64.urlsafe_b64encode(json.dumps([scope, value]).encode()).decode()

        scope = ["workflow_report"]
        key = ["2026-11-15T10:00:00+00:00", 17]
        self.assertEqual(decode(cursor(scope, key), scope, (datetime, int)),
                         [datetime.fromisoformat(key[0]), 17])
        for invalid in (cursor(["other"], key), cursor(scope, ["2026-11-15", 17]),
                        cursor(scope, [key[0], True]), cursor(scope, [key[0], 2**63]), "%%%"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(WorkflowError, "INVALID_CURSOR"):
                decode(invalid, scope, (datetime, int))

    def test_catalog_rejects_traversal_absolute_paths_and_symlinks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "approved.json").write_text("{}")
            (root / "redirect").symlink_to(root / "approved.json")
            self.assertEqual(safe(root, "approved.json"), root / "approved.json")
            for invalid in ("../outside", str(root / "approved.json"), "redirect"):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    safe(root, invalid)

    def test_stage_execution_keys_keep_order_and_include_pinned_versions(self):
        spec = {"inputs": [{"source": "a", "manifest": "left"}, {"source": "b", "manifest": "right"}],
                "ingestion_contract": 1, "ruleset_sha256": "rules",
                "quality_revision": "engine", "matching_execution": "model-and-revision"}
        rows = [{"batch_id": 2}, {"batch_id": 9}]
        self.assertEqual([stages.key(spec, i, rows) for i in range(5)], [
            ["a", "left", 1], ["b", "right", 1], [2, "rules", "engine"],
            [9, "rules", "engine"], [2, 9, "model-and-revision"],
        ])
