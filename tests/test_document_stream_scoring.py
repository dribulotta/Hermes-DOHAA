import unittest

from tools.document_stream import DocumentEvent
from tools.document_stream_scoring import ReferenceFact, score_report


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.active = {"notice": DocumentEvent("e2", "notice", 2, 2, "Friday.")}
        self.expected = [ReferenceFact("delivery_day", "Friday", ((('notice', 2),),))]
        self.report = {"facts": [{"key": "delivery_day", "value": "Friday",
                                 "evidence": [{"source_id": "notice", "revision": 2}]}]}

    def test_correct_supported_report(self):
        score = score_report(self.report, self.expected, self.active)
        self.assertTrue(score["task_success"])
        self.assertEqual(score["correct_facts"], 1)

    def test_correct_value_with_stale_citation_fails(self):
        self.report["facts"][0]["evidence"][0]["revision"] = 1
        score = score_report(self.report, self.expected, self.active)
        self.assertFalse(score["task_success"])
        self.assertEqual(score["stale_or_unknown_evidence_facts"], 1)
        self.assertEqual(score["missing_critical_facts"], 1)

    def test_current_citation_with_wrong_value_fails(self):
        self.report["facts"][0]["value"] = "Tuesday"
        score = score_report(self.report, self.expected, self.active)
        self.assertEqual(score["wrong_value_facts"], 1)
        self.assertEqual(score["correct_facts"], 0)

    def test_abstention_counts_omissions(self):
        score = score_report({"facts": []}, self.expected, self.active)
        self.assertFalse(score["task_success"])
        self.assertEqual(score["missing_facts"], 1)

    def test_extra_fact_prevents_success(self):
        self.report["facts"].append({"key": "price", "value": "free", "evidence": []})
        score = score_report(self.report, self.expected, self.active)
        self.assertFalse(score["task_success"])
        self.assertEqual(score["extra_facts"], 1)

    def test_duplicate_fact_invalidates_report(self):
        self.report["facts"] *= 2
        self.assertFalse(score_report(self.report, self.expected, self.active)["valid_format"])

    def test_invalid_shapes_fail_closed(self):
        for report in (None, [], {"facts": [], "extra": True}, {"facts": "wrong"},
                       {"facts": [{"key": "delivery_day", "value": True, "evidence": []}]}):
            with self.subTest(report=report):
                result = score_report(report, self.expected, self.active)
                self.assertFalse(result["valid_format"])
                self.assertEqual(result["correct_facts"], 0)

    def test_duplicate_citations_invalid(self):
        self.report["facts"][0]["evidence"] *= 2
        self.assertFalse(score_report(self.report, self.expected, self.active)["valid_format"])

    def test_reference_must_be_available_and_unambiguous(self):
        with self.assertRaises(ValueError):
            score_report(self.report, self.expected, {})
        with self.assertRaises(ValueError):
            score_report(self.report, self.expected * 2, self.active)

    def test_reference_type_validation(self):
        for key, value, evidence in (("x", True, ((("notice", 2),),)),
                                     ("x", "Friday", ()),
                                     ("x", "Friday", ((("notice", True),),))):
            with self.assertRaises(ValueError):
                ReferenceFact(key, value, evidence)

    def test_supported_alternative_evidence(self):
        self.active["other"] = DocumentEvent("e3", "other", 1, 2, "Friday.")
        refs = [ReferenceFact("delivery_day", "Friday",
                              ((("other", 1),), (("notice", 2),)))]
        self.assertTrue(score_report(self.report, refs, self.active)["task_success"])

    def test_unrelated_current_evidence_is_not_semantic_support(self):
        self.active["other"] = DocumentEvent("e3", "other", 1, 2, "Unrelated.")
        self.report["facts"][0]["evidence"] = [{"source_id": "other", "revision": 1}]
        score = score_report(self.report, self.expected, self.active)
        self.assertEqual(score["unsupported_facts"], 1)
        self.assertFalse(score["task_success"])

    def test_empty_reference_requires_empty_report(self):
        self.assertTrue(score_report({"facts": []}, [], {})["task_success"])
        self.assertFalse(score_report(self.report, [], self.active)["task_success"])


if __name__ == "__main__":
    unittest.main()
