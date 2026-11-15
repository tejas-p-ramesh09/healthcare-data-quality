import unittest

from healthcare_dq.matching.core import block, compare, decide, exact, features, fit, phone, score
from healthcare_dq.matching.evaluation import report
from healthcare_dq.matching.pipeline import choose


class MatchingTests(unittest.TestCase):
    def test_metadata_has_no_effect(self):
        raw = {"name": [{"given": ["Ａna"], "family": "Doe"}], "birthDate": "2000-02-29"}
        original = features(raw)
        changed = features({**raw, "id": "different", "identifier": [{"value": "anything"}],
                            "meta": {"source": "truth"}, "truth": "x", "path": "a"})
        self.assertEqual(original, changed)
        self.assertTrue(exact(original, changed))
        self.assertEqual(compare(original, changed)["phone"], "missing")

    def test_block_limits_and_competition(self):
        f = features({"telecom": [{"system": "phone", "value": "+1 (202) 555-0199"}]})
        candidates, emissions = block({"a": f}, {"b": f, "c": f})
        self.assertEqual((len(candidates), emissions), (2, 2))
        with self.assertRaisesRegex(ValueError, "OVERSIZED_BLOCK"):
            block({"a": f}, {"b": f, "c": f}, max_block=1)
        self.assertEqual(decide({("a", "b"): 3, ("a", "c"): 3}, 0, 2, 0)[("a", "b")][0],
                         "review")

    def test_oracle_blocked_positive_false_link_and_review(self):
        truth = {"left": {"a": "p", "b": "q", "c": "u"}, "right": {"d": "p", "e": "q", "f": "v"},
                 "families": {"p": "format", "q": "combined"}, "hard_negatives": [["c", "f", "phone"]]}
        decisions = {("a", "d"): ("suggested_match", ""), ("a", "e"): ("review", ""),
                     ("c", "f"): ("suggested_match", "")}
        result = report(truth, decisions)
        self.assertEqual((result["full"]["tp"], result["full"]["fp"], result["full"]["fn"]), (1, 1, 1))
        self.assertEqual(result["full"]["tn"], 6)
        self.assertEqual(result["blocking_recall"], .5)
        self.assertEqual(result["candidate_only"]["recall"], 1)

    def test_smoothed_score_and_partial_date(self):
        p = features({"birthDate": "2000"})
        q = features({"birthDate": "2000-01-01"})
        n = features({"birthDate": "2001"})
        a, b = compare(p, q), compare(p, n)
        self.assertEqual(a["birth"], "close")
        self.assertEqual(b["birth"], "conflict")
        model = fit([(a, True), (b, False)])
        value, components = score(a, model)
        self.assertAlmostEqual(value, sum(components.values()))
        self.assertGreater(value, score(b, model)[0])

    def test_supported_phone_formats(self):
        for value in ("2025550199", "+1 (202) 555-0199", "1-202-555-0199"):
            self.assertEqual(phone(value), "2025550199")
        for value in (None, "", "202+555+0199", "++1 2025550199", "+44 2025550199", "+1202555019"):
            self.assertEqual(phone(value), "")

    def test_review_fallback_keeps_ties_together_and_never_suggests(self):
        ids = {str(i): str(i) for i in range(21)}
        truth = {"left": ids, "right": ids, "families": dict.fromkeys(ids, "format"), "hard_negatives": []}
        for count, expected in ((0, 0), (4, 4), (20, 20), (21, 0)):
            with self.subTest(count=count):
                rows = {("0", str(i)): {"exact": 1} for i in range(count)}
                policy, result, _ = choose(rows, truth, "exact")
                self.assertTrue(policy["review_only"])
                self.assertEqual(result["dispositions"].get("review", 0), expected)
                self.assertEqual(sum(result["full"][k] for k in ("tp", "fp")), 0)
                self.assertEqual(decide({("a", "b"): 10}, **policy)["a", "b"][0], "review")

    def test_decision_boundaries(self):
        scores = {("a", "x"): 3, ("a", "y"): 2.5, ("b", "z"): 1, ("c", "w"): 0.99}
        result = decide(scores, 1, 2, .5)
        self.assertEqual(result["a", "x"], ("review", "COMPETING_CANDIDATE"))
        self.assertEqual(result["b", "z"], ("review", "BETWEEN_THRESHOLDS"))
        self.assertEqual(result["c", "w"][0], "unlikely_match")
