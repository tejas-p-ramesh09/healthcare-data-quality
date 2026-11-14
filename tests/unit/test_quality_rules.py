import unittest
from unittest.mock import patch

from healthcare_dq.quality.engine import Record, evaluate
from healthcare_dq.quality.rules import RULES, check


class RuleTests(unittest.TestCase):
    def test_predicates_and_boundaries(self):
        start = "2025-01-01T00:00:00Z"
        end = {"period": {"end": start}}
        cases = (
            (0, {}, None, "finding"), (0, {"birthDate": "2000"}, None, "pass"),
            (1, {"code": {"text": "Example"}}, None, "finding"),
            (1, {"code": {"coding": [{"system": "urn:example", "code": "test"}]}}, None, "pass"),
            (2, {}, None, "not_applicable"), (2, {}, end, "unable_to_assess"),
            (2, {"effectiveDateTime": "2025-01-02T01:00:00+01:00"}, end, "pass"),
            (2, {"effectiveDateTime": "2025-01-02T00:00:01Z"}, end, "finding"),
            (3, {"onsetDateTime": "2025"}, None, "unable_to_assess"),
            (3, {"onsetDateTime": start, "abatementDateTime": start}, None, "pass"),
            (3, {"onsetDateTime": start, "abatementDateTime": "2024-12-31T23:00:00Z"}, None, "finding"),
            (4, {"intent": "proposal"}, None, "not_applicable"),
            (4, {"intent": "order"}, None, "finding"),
            (4, {"intent": "order", "authoredOn": start}, None, "pass"),
        )
        for rule, data, encounter, expected in cases:
            with self.subTest(rule=rule, data=data):
                self.assertEqual(check(RULES[rule], data, encounter, 24)[0], expected)

    def test_unexpected_rule_exception_propagates(self):
        with patch("healthcare_dq.quality.engine.check", side_effect=RuntimeError("unexpected")), \
                self.assertRaises(RuntimeError):
            evaluate([Record("a.json", 0, "Patient", "a", {})], {"encounter_grace_hours": 24})
