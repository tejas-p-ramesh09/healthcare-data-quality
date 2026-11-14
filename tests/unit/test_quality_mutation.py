import unittest
from datetime import timedelta

from healthcare_dq.fhir import prepare
from healthcare_dq.quality.mutation import candidates
from tests.unit.test_ingestion_inputs import entry, file, ref


class EligibilityTests(unittest.TestCase):
    def eligible(self, *rows):
        return {(e.identifier, rule) for e, rule, *_ in candidates(prepare([file(*rows)]),
                                                                   timedelta(hours=24))}

    def test_missing_and_null_optional_fields_are_ineligible(self):
        rows = [entry("Patient", "missing"), entry("Patient", "null", birthDate=None),
                entry("Patient", "partial", birthDate="2000")]
        for name, fields in (("m1", {}), ("m2", {"authoredOn": None}), ("m3", {"authoredOn": "2025"})):
            rows.append(entry("MedicationRequest", name, subject=ref("Patient/missing"),
                              status="active", intent="order", **fields))
        self.assertEqual(self.eligible(*rows), {
            ("partial", "PATIENT_BIRTH_DATE_MISSING"), ("m3", "MEDICATION_ORDER_AUTHORED_ON_MISSING"),
        })

    def test_coding_requires_a_usable_member_without_assuming_object_shape(self):
        codes = ({}, {"coding": None}, {"coding": 9}, {"coding": {"system": "x"}},
                 {"coding": [None, 7, {"system": 1, "code": "x"}, {"system": "s", "code": None}]},
                 {"coding": [None, {"system": "s", "code": "x"}]})
        rows = [entry("Observation", f"o{i}", subject=ref("Patient/p"), status="final", code=code)
                for i, code in enumerate(codes)]
        self.assertEqual(self.eligible(entry("Patient", "p"), *rows),
                         {("o5", "OBSERVATION_CODE_NO_USABLE_CODING")})

    def test_timestamp_contract_and_preconditions(self):
        start = "2025-01-01T00:00:00Z"
        times = ("2025-01-02T01:00:00+01:00", "2025-01-02T00:00:00.000001Z",
                 "2025-01-02T00:00Z", "2025-01-02T00:00:00.0000001Z",
                 "2025-01-02T00:00:00+0000", "2025-01-01T00:00:00", "2025", None)
        rows = [entry("Observation", f"o{i}", subject=ref("Patient/p"), encounter=ref("Encounter/e"),
                      status="final", code={}, effectiveDateTime=value) for i, value in enumerate(times)]
        rows.extend([
            entry("Condition", "same", subject=ref("Patient/p"), onsetDateTime=start,
                  abatementDateTime="2025-01-01T01:00:00+01:00"),
            entry("Condition", "before", subject=ref("Patient/p"), onsetDateTime=start,
                  abatementDateTime="2024-12-31T23:59:59Z"),
            entry("Condition", "underflow", subject=ref("Patient/p"), onsetDateTime="0001-01-01T00:00:00Z",
                  abatementDateTime=start),
        ])
        self.assertEqual(self.eligible(entry("Patient", "p"), entry(
            "Encounter", "e", subject=ref("Patient/p"), status="finished", period={"end": start}), *rows),
            {("o0", "OBSERVATION_AFTER_ENCOUNTER_END"), ("same", "CONDITION_ABATEMENT_BEFORE_ONSET")})
