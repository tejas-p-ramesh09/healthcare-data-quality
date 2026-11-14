import hashlib
import json
import unittest

from healthcare_dq.fhir import prepare
from healthcare_dq.inputs import InputError, InputFile, manifest

ALIAS = "urn:uuid:11111111-1111-1111-1111-111111111111"


def entry(kind, identifier, alias=None, **fields):
    result = {"resource": {"resourceType": kind, "id": identifier, **fields}}
    if alias:
        result["fullUrl"] = alias
    return result


def file(*entries, path="bundle.json"):
    data = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": entries}).encode()
    return InputFile(path, data, hashlib.sha256(data).hexdigest())


def ref(value):
    return {"reference": value}


class InputTests(unittest.TestCase):
    def test_manifest_is_order_independent_but_path_and_bytes_matter(self):
        a = file(entry("Patient", "p"))
        b = file(entry("Patient", "q"), path="b")
        self.assertEqual(manifest([a, b]), manifest([b, a]))
        self.assertNotEqual(manifest([a]), manifest([file(entry("Patient", "q"))]))
        self.assertNotEqual(manifest([a]), manifest([file(entry("Patient", "p"), path="renamed")]))

    def test_bundle_local_aliases_and_forward_batch_relative_references(self):
        a = file(entry("Encounter", "e", subject=ref(ALIAS), status="finished"),
                 entry("Patient", "a", ALIAS))
        b = file(entry("Observation", "o", subject=ref(ALIAS), status="final", code={}),
                 entry("Patient", "b", ALIAS),
                 entry("Condition", "c", subject=ref("Patient/a"), encounter=ref("Encounter/e")),
                 path="b.json")
        actual = {(e.kind, e.identifier): e.fields for e in prepare([b, a])}
        self.assertEqual(actual["Encounter", "e"]["patient_id"], "a")
        self.assertEqual(actual["Observation", "o"]["patient_id"], "b")
        self.assertEqual(actual["Condition", "c"]["encounter_id"], "e")

    def test_invalid_reference_and_identity_cases(self):
        patient = entry("Patient", "a", ALIAS)
        encounter = entry("Encounter", "e", subject=ref(ALIAS), status="finished")
        for code, files in (
            ("AMBIGUOUS_ALIAS", [file(patient, entry("Patient", "b", ALIAS))]),
            ("DUPLICATE_ID", [file(patient), file(entry("Patient", "a"), path="b.json")]),
            ("WRONG_TARGET_TYPE", [file(entry("Encounter", "e", ALIAS, subject=ref(ALIAS),
                                             status="finished"))]),
            ("WRONG_TARGET_TYPE", [file(entry("Condition", "c", subject=ref("Encounter/e")))]),
            ("UNRESOLVED_REFERENCE", [file(patient), file(encounter, path="b.json")]),
            ("UNSUPPORTED_REFERENCE", [file(entry("Condition", "c", subject=ref("https://a/Patient/a")))]),
            ("CROSS_PATIENT_ENCOUNTER", [file(patient, encounter, entry("Patient", "b"),
                                             entry("Condition", "c", subject=ref("Patient/b"),
                                                   encounter=ref("Encounter/e")))]),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(InputError, code):
                prepare(files)

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaisesRegex(InputError, "INVALID_JSON"):
            prepare([InputFile("bad.json", b'{"type":1,"type":2}', "")])
