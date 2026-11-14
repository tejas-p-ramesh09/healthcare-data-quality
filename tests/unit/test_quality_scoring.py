import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from healthcare_dq.quality.evaluation import load_manifest, score
from healthcare_dq.quality.rules import RULES


def finding(index, rule=0):
    return {"rule_id": RULES[rule].code, "rule_version": 1,
            "path": "fixture.json", "entry_index": index, "pointer": RULES[rule].pointer}


class ScoringTests(unittest.TestCase):
    def test_scope_baseline_duplicates_and_zero_denominators(self):
        labels = [{**finding(i), "positive": i in (0, 1)} for i in range(4)]
        before = [finding(4)]
        after = [finding(0), finding(1, rule=1), finding(2), finding(4)]
        report = score(labels, before, after)
        self.assertEqual(report["micro"], {
            "tp": 1, "fp": 1, "fn": 1, "tn": 1, "support": 2,
            "precision": 0.5, "recall": 0.5, "f1": 0.5,
        })
        self.assertEqual(report["unchanged_baseline"], 1)
        self.assertEqual(len(report["new_unscored"]), 1)
        self.assertIsNone(report["by_rule"][RULES[1].code]["recall"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            score(labels, before, after + [after[0]])

    def test_frozen_manifest_checksum_rejects_tampering(self):
        original = b'{"labels":[]}'
        digest = hashlib.sha256(original).hexdigest()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_bytes(original)
            self.assertEqual(load_manifest(path, digest), {"labels": []})
            path.write_bytes(b'{"labels":[{}]}')
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_manifest(path, digest)
