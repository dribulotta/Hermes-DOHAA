import json
import tempfile
import unittest
from pathlib import Path

from hermes_dohaa.evaluation.models import EvaluationSuite
from tools.build_runtime_stability_suite_v1 import generated_artifacts
from tools.validate_runtime_stability_suite_v1 import (
    DEFAULT_MANIFEST,
    DEFAULT_PUBLIC_REFERENCE,
    DEFAULT_SUITE,
    RuntimeStabilitySuiteError,
    validate_runtime_stability_suite,
)


class RuntimeStabilitySuiteTests(unittest.TestCase):
    def test_committed_artifacts_are_deterministic(self):
        for path, expected in generated_artifacts().items():
            self.assertEqual(expected, path.read_text(encoding="utf-8"), path)

    def test_suite_and_manifest_pass_offline_validation(self):
        result = validate_runtime_stability_suite()

        self.assertTrue(result["valid"])
        self.assertEqual(16, result["case_count"])
        self.assertEqual(300, result["timeout_seconds"])
        self.assertEqual(1, result["smoke_repetitions"])
        self.assertEqual(3, result["soak_repetitions"])
        self.assertEqual(
            {
                "evidence_synthesis": 4,
                "quantitative_reconciliation": 4,
                "structured_extraction": 4,
                "temporal_reasoning": 4,
            },
            result["domain_counts"],
        )
        self.assertGreaterEqual(result["semantic_assertion_count"], 48)
        self.assertGreaterEqual(result["contract_bytes"]["minimum"], 2400)
        self.assertLessEqual(result["contract_bytes"]["maximum"], 4500)

    def test_changed_case_order_is_rejected(self):
        raw = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
        raw["cases"][0], raw["cases"][1] = raw["cases"][1], raw["cases"][0]
        self._assert_invalid(raw, json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8")), "interleaved")

    def test_changed_expected_result_is_rejected(self):
        raw = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
        raw["cases"][0]["expected_result"]["verified_available_units"] += 1
        manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        manifest["suite_canonical_sha256"] = EvaluationSuite.from_dict(raw).sha256()
        self._assert_invalid(raw, manifest, "visible gates")

    def test_stale_manifest_hash_is_rejected(self):
        manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        manifest["suite_canonical_sha256"] = "0" * 64
        self._assert_invalid(json.loads(DEFAULT_SUITE.read_text(encoding="utf-8")), manifest, "manifest")

    def test_suite_contains_no_protected_marker(self):
        raw = DEFAULT_SUITE.read_text(encoding="utf-8").casefold()
        for marker in (
            "candidate-04",
            "candidate_04",
            "protected-multimodel-holdout",
            "protected_case",
        ):
            self.assertNotIn(marker, raw)

    def _assert_invalid(self, suite, manifest, message):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            manifest_path = root / "manifest.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeStabilitySuiteError, message):
                validate_runtime_stability_suite(
                    suite_path,
                    manifest_path,
                    DEFAULT_PUBLIC_REFERENCE,
                )


if __name__ == "__main__":
    unittest.main()
