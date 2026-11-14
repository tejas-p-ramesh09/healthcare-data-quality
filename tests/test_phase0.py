from collections import Counter
from contextlib import redirect_stdout
import copy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import phase0
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


class ConfigurationTests(unittest.TestCase):
    def test_malformed_configurations_report_the_file_and_problem(self):
        settings = phase0.load_settings(phase0.CONFIG / "run.json")
        missing = dict(settings)
        del missing["jar_url"]
        cases = (
            ("{", "could not read configuration"),
            ("[]", "expected a JSON object"),
            (json.dumps(missing), "missing required key 'jar_url'"),
            (json.dumps(dict(settings, timezone=0)), "'timezone' must be a nonempty string"),
            (json.dumps(dict(settings, location=" ")), "'location' must be a nonempty string"),
            (json.dumps(dict(settings, population="5")), "'population' must be an integer"),
            (json.dumps(dict(settings, clinician_seed=True)), "'clinician_seed' must be an integer"),
            (json.dumps(dict(settings, population=0)), "'population' must be positive"),
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            for raw, message in cases:
                with self.subTest(message=message):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, f"run.json: {message}"):
                        phase0.load_settings(path)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.jar = root / "synthea.jar"
        self.jar.write_bytes(b"test input, not an executable")
        self.output = root / "output"
        self.java = root / "unused-java"
        settings = phase0.load_settings(phase0.CONFIG / "run.json")
        self.settings = dict(settings, jar_sha256=sha256(self.jar))
        self.enterContext(patch.object(phase0, "CONFIG", root))
        self.enterContext(redirect_stdout(StringIO()))

    def write_settings(self):
        path = phase0.CONFIG / "run.json"
        path.write_text(json.dumps(self.settings), encoding="utf-8")

    def test_rejects_mismatched_checksum_before_generating(self):
        self.settings["jar_sha256"] = "0" * 64
        self.write_settings()
        with patch.object(phase0, "generate") as generate:
            with self.assertRaisesRegex(ValueError, "JAR checksum mismatch"):
                phase0.run(self.java, self.jar, self.output)
        generate.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_rejects_unexpected_patient_count(self):
        self.write_settings()
        inventory_result = ({}, Counter(), 1)
        with patch.object(phase0, "generate", return_value=inventory_result) as generate:
            with self.assertRaisesRegex(ValueError, "baseline: expected 5 distinct patients"):
                phase0.run(self.java, self.jar, self.output)
        generate.assert_called_once()
        self.assertFalse((self.output / "report.json").exists())

    def test_rejects_different_patient_projection_with_same_count(self):
        self.write_settings()
        baseline = {
            f"synthetic-{index}": ("1990-01-01", self.output / "unused.json", index)
            for index in range(self.settings["population"])
        }
        repeat = baseline.copy()
        repeat["synthetic-0"] = ("1991-01-01", self.output / "unused.json", 0)
        inventories = [(records, Counter(Patient=len(records)), 1)
                       for records in (baseline, repeat)]
        with patch.object(phase0, "generate", side_effect=inventories) as generate:
            with self.assertRaisesRegex(ValueError, "Patient ID and birthDate projections differ"):
                phase0.run(self.java, self.jar, self.output)
        self.assertEqual(generate.call_count, 2)
        self.assertFalse((self.output / "mutated.json").exists())
