import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.phase0 import RULE, findings, inject, inventory, read_bundle, sha256


def bundle(*resources):
    return {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}


def patient(identifier="synthetic-a"):
    return {"resourceType": "Patient", "id": identifier, "birthDate": "1990-01-01"}


class PhaseZeroTests(unittest.TestCase):
    def test_baseline_and_non_patient(self):
        self.assertEqual(findings(bundle(patient(), {"resourceType": "Observation"})), [])

    def test_missing_null_and_blank_birth_dates(self):
        for value in (None, "", "  ", "absent"):
            with self.subTest(value=value):
                record = patient()
                if value == "absent":
                    del record["birthDate"]
                else:
                    record["birthDate"] = value
                self.assertEqual(findings(bundle(record)), [{
                    "code": RULE, "patient_id": "synthetic-a",
                    "path": "/entry/0/resource/birthDate",
                }])

    def test_wrong_birth_date_type_is_an_input_error(self):
        record = patient()
        record["birthDate"] = 123
        with self.assertRaisesRegex(ValueError, "birthDate"):
            findings(bundle(record))

    def test_injection_preserves_source_and_changes_only_one_field(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            destination = Path(tmp) / "injected.json"
            original = bundle(patient(), patient("synthetic-b"))
            source.write_text(json.dumps(original), encoding="utf-8")
            digest = sha256(source)

            path = inject(source, destination, "synthetic-b")

            self.assertEqual(sha256(source), digest)
            expected = copy.deepcopy(original)
            del expected["entry"][1]["resource"]["birthDate"]
            mutated = read_bundle(destination)
            self.assertEqual(mutated, expected)
            self.assertEqual(path, "/entry/1/resource/birthDate")
            self.assertEqual(findings(mutated), [{
                "code": RULE, "patient_id": "synthetic-b", "path": path,
            }])
            with self.assertRaises(FileExistsError):
                inject(source, destination, "synthetic-b")
            self.assertEqual(read_bundle(destination), expected)

    def test_injection_rejects_missing_or_ambiguous_target(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            destination = Path(tmp) / "injected.json"
            for records in (bundle(patient("different")), bundle(patient(), patient())):
                with self.subTest(records=records):
                    source.write_text(json.dumps(records), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "exactly one"):
                        inject(source, destination, "synthetic-a")
                    self.assertFalse(destination.exists())

    def test_invalid_json_and_bundle_shape(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for value in ("{", "[]", '{"resourceType":"Bundle","entry":[null]}'):
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_bundle(path)

    def test_inventory_rejects_empty_export_and_duplicate_patient_ids(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "No FHIR JSON"):
                inventory(root)
            for filename in ("a.json", "b.json"):
                (root / filename).write_text(json.dumps(bundle(patient())), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate Patient.id"):
                inventory(root)
