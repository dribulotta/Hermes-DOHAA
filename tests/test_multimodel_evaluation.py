import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.cli import main
from hermes_dohaa.evaluation import (
    EvaluationProtocol,
    EvaluationSuite,
    ModelManifest,
    ModelManifestError,
    MultimodelEvaluationError,
    SuiteCommitment,
    analyze_multimodel_results,
    assess_success,
    run_multimodel_evaluation,
    write_model_manifest,
    write_suite_commitment,
)
from hermes_dohaa.runtime.base import Proposal
from hermes_dohaa.runtime.hermes_api import HermesApiRuntime


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO_ROOT / "examples/multimodel-evaluation-protocol.json"
MANIFEST_EXAMPLE_PATH = (
    REPO_ROOT / "examples/multimodel-model-manifest.example.json"
)
MANIFEST_SCHEMA_PATH = REPO_ROOT / "schemas/model-manifest-draft.schema.json"


def protocol():
    return EvaluationProtocol.from_json_file(PROTOCOL_PATH)


def model_dicts():
    models = json.loads(MANIFEST_EXAMPLE_PATH.read_text(encoding="utf-8"))[
        "models"
    ]
    for index, model in enumerate(models, 1):
        model["model_alias"] = f"synthetic-model-{index}"
        model["model_artifact_id"] = f"synthetic-artifact-{index}-sha256"
        model["provider"] = "synthetic-provider"
        model["backend"] = "synthetic-backend"
        model["backend_version"] = "1.0.0"
        model["architecture"]["family"] = f"synthetic-family-{index}"
        model["quantization"] = "synthetic-q4"
    return models


def manifest_draft():
    selected_protocol = protocol()
    return {
        "schema_version": "1.0",
        "protocol_id": selected_protocol.protocol_id,
        "protocol_sha256": selected_protocol.sha256(),
        "models": model_dicts(),
    }


def protected_suite():
    domains = (
        "evidence_synthesis",
        "quantitative_reconciliation",
        "structured_extraction",
        "temporal_reasoning",
    )
    cases = []
    for index in range(48):
        value = index + 1
        cases.append(
            {
                "case_id": f"synthetic-multimodel-{index:02d}",
                "domain": domains[index % len(domains)],
                "contract": {
                    "schema_version": "1.0",
                    "contract_id": f"synthetic-multimodel-contract-{index:02d}",
                    "objective": "Return the visible answer.",
                    "inputs": {
                        "answer": {"value": value},
                        "result_spec": {
                            "spec_version": "2.0",
                            "type": "object",
                            "required": ["value"],
                            "additional_properties": False,
                            "properties": {"value": {"type": "integer"}},
                        },
                    },
                    "constraints": ["Do not request actions."],
                    "acceptance_criteria": [
                        {
                            "criterion_id": "visible-answer",
                            "description": "Return the visible answer.",
                            "required_evidence": [],
                        }
                    ],
                    "allowed_actions": [],
                    "forbidden_actions": ["shell.execute"],
                    "risk_level": "low",
                    "max_attempts": 2,
                    "requires_human_approval": False,
                },
                "expected_result": {"value": value},
            }
        )
    return EvaluationSuite.from_dict(
        {
            "schema_version": "1.0",
            "suite_id": "synthetic-preregistered-suite",
            "description": "Synthetic 48-case multi-model test suite.",
            "cases": cases,
        }
    )


class ExactRuntime:
    def __init__(self):
        self.usage_records = []

    def propose(self, contract, feedback):
        del feedback
        self.usage_records.append({"total_tokens": 10})
        return Proposal(dict(contract.inputs["answer"]))


def model_run(slot_id, direct, dohaa, *, token_usage=True):
    trials = []
    for index, (direct_passed, dohaa_passed) in enumerate(zip(direct, dohaa)):
        conditions = {}
        for condition, passed, calls in (
            ("direct", direct_passed, 1),
            ("self_reflection", direct_passed, 2),
            ("dohaa", dohaa_passed, 1),
        ):
            usage = []
            if token_usage:
                total = 12 if condition == "dohaa" else 10
                usage = [{"total_tokens": total} for _ in range(calls)]
            conditions[condition] = {
                "status": "completed",
                "runtime_calls": calls,
                "usage": usage,
                "final_score": {"all_gates_passed": passed},
            }
        trials.append(
            {
                "case_id": f"case-{index}",
                "domain": "synthetic",
                "conditions": conditions,
            }
        )
    return {
        "slot_id": slot_id,
        "model_alias": f"alias-{slot_id}",
        "model_artifact_id": f"artifact-{slot_id}",
        "evaluation": {
            "cases": trials,
            "summary": {"dohaa": {"regressed": 0}},
        },
    }


class MultimodelEvaluationTests(unittest.TestCase):
    def test_manifest_is_canonical_private_and_bound_to_protocol(self):
        selected_protocol = protocol()
        manifest = ModelManifest.create(selected_protocol, model_dicts())
        exported = manifest.to_dict()
        exported["models"][0]["model_alias"] = "mutated"

        self.assertEqual(
            manifest.models[0].model_alias,
            "synthetic-model-1",
        )
        self.assertEqual(len(manifest.sha256()), 64)
        manifest.verify(selected_protocol)

        substituted = manifest.to_dict()
        substituted["models"][1]["model_artifact_id"] = substituted["models"][0][
            "model_artifact_id"
        ]
        with self.assertRaisesRegex(ModelManifestError, "identities must be unique"):
            ModelManifest.from_dict(substituted).verify(selected_protocol)

        wrong_protocol = selected_protocol.to_dict()
        wrong_protocol["description"] = "A different canonical protocol."
        with self.assertRaisesRegex(ModelManifestError, "protocol_sha256"):
            manifest.verify(EvaluationProtocol.from_dict(wrong_protocol))

    def test_cli_freezes_manifest_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            draft_path = Path(directory) / "draft.json"
            draft_path.write_text(json.dumps(manifest_draft()), encoding="utf-8")
            output_path = Path(directory) / "manifest.json"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "freeze-model-manifest",
                        str(draft_path),
                        "--protocol",
                        str(PROTOCOL_PATH),
                        "--output",
                        str(output_path),
                    ]
                )
            payload = json.loads(output.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "frozen")
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            frozen = ModelManifest.from_json_file(output_path)
            frozen.verify(protocol())

            output = io.StringIO()
            with redirect_stdout(output):
                second_exit = main(
                    [
                        "freeze-model-manifest",
                        str(draft_path),
                        "--protocol",
                        str(PROTOCOL_PATH),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(second_exit, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    def test_multimodel_runner_uses_every_frozen_model_and_protocol_policy(self):
        selected_protocol = protocol()
        manifest = ModelManifest.create(selected_protocol, model_dicts())
        suite = protected_suite()
        commitment = SuiteCommitment.create(suite, protocol_commit="a" * 40)
        built_slots = []

        def builder(model):
            built_slots.append(model.slot_id)

            def factory(contract, session_id, sampling_seed):
                del contract, session_id, sampling_seed
                return ExactRuntime()

            return factory

        result = run_multimodel_evaluation(
            suite,
            commitment,
            selected_protocol,
            manifest,
            builder,
            runtime_context={"adapter": "synthetic"},
        )

        self.assertEqual(
            built_slots,
            [slot.slot_id for slot in selected_protocol.model_slots],
        )
        self.assertEqual(len(result["model_runs"]), 3)
        self.assertEqual(result["aggregate_analysis"]["unique_cases"], 48)
        self.assertEqual(
            set(result["aggregate_analysis"]["domain_statistics"]),
            {
                "evidence_synthesis",
                "quantitative_reconciliation",
                "structured_extraction",
                "temporal_reasoning",
            },
        )
        self.assertEqual(
            result["model_runs"][0]["evaluation"]["seed"],
            selected_protocol.execution_policy["condition_order_seed"],
        )
        self.assertEqual(
            result["model_runs"][0]["evaluation"]["runtime_policy"][
                "model_artifact_id"
            ],
            manifest.models[0].model_artifact_id,
        )
        self.assertFalse(result["success_assessment"]["passed"])
        self.assertIn(
            "primary_p_below_alpha",
            result["success_assessment"]["unevaluable_criteria"],
        )

    def test_multimodel_cli_writes_one_private_non_overwriting_result(self):
        selected_protocol = protocol()
        manifest = ModelManifest.create(selected_protocol, model_dicts())
        suite = protected_suite()
        commitment = SuiteCommitment.create(suite, protocol_commit="c" * 40)

        def exact_proposal(runtime, contract, feedback):
            del feedback
            runtime.usage_records.append({"total_tokens": 10})
            return Proposal(dict(contract.inputs["answer"]))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            commitment_path = root / "suite.commitment.json"
            manifest_path = root / "models.json"
            output_path = root / "result.json"
            suite_path.write_text(
                json.dumps(suite.to_dict()), encoding="utf-8"
            )
            write_suite_commitment(commitment_path, commitment)
            write_model_manifest(manifest_path, manifest)
            output = io.StringIO()
            with patch.object(
                HermesApiRuntime,
                "propose",
                autospec=True,
                side_effect=exact_proposal,
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "evaluate-multimodel",
                        str(suite_path),
                        "--suite-commitment",
                        str(commitment_path),
                        "--protocol",
                        str(PROTOCOL_PATH),
                        "--model-manifest",
                        str(manifest_path),
                        "--output",
                        str(output_path),
                    ]
                )

            payload = json.loads(output.getvalue())
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(len(result["model_runs"]), 3)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            self.assertEqual(
                result["model_runs"][0]["evaluation"]["repetitions"], 1
            )

            with redirect_stdout(io.StringIO()):
                second_exit = main(
                    [
                        "evaluate-multimodel",
                        str(suite_path),
                        "--suite-commitment",
                        str(commitment_path),
                        "--protocol",
                        str(PROTOCOL_PATH),
                        "--model-manifest",
                        str(manifest_path),
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(second_exit, 2)

    def test_suite_mismatch_fails_before_runtime_builder(self):
        selected_protocol = protocol()
        manifest = ModelManifest.create(selected_protocol, model_dicts())
        suite = protected_suite()
        commitment = SuiteCommitment.create(suite, protocol_commit="b" * 40)
        altered = EvaluationSuite(
            schema_version=suite.schema_version,
            suite_id=suite.suite_id,
            description=suite.description,
            cases=suite.cases[:-1],
        )
        called = False

        def builder(model):
            nonlocal called
            called = True
            raise AssertionError(model)

        with self.assertRaises(MultimodelEvaluationError):
            run_multimodel_evaluation(
                altered,
                commitment,
                selected_protocol,
                manifest,
                builder,
            )
        self.assertFalse(called)

    def test_global_aggregation_and_criteria_are_preregistered(self):
        selected_protocol = protocol()
        slots = [slot.slot_id for slot in selected_protocol.model_slots]
        model_runs = [
            model_run(slots[0], [False] * 8, [True] * 8),
            model_run(slots[1], [False, True] * 4, [True] * 8),
            model_run(slots[2], [True] * 8, [True] * 8),
        ]
        analysis = analyze_multimodel_results(selected_protocol, model_runs)
        assessment = assess_success(selected_protocol, model_runs, analysis)

        self.assertEqual(analysis["unique_cases"], 8)
        self.assertEqual(analysis["primary_comparison"]["wins"], 8)
        self.assertEqual(analysis["primary_comparison"]["losses"], 0)
        self.assertEqual(
            analysis["primary_comparison"]["exact_two_sided_sign_test_p"],
            0.0078125,
        )
        self.assertTrue(assessment["passed"])
        self.assertEqual(assessment["status"], "passed")
        self.assertTrue(assessment["token_usage_complete"])

        model_runs[0]["evaluation"]["cases"][0]["conditions"]["dohaa"][
            "usage"
        ] = []
        assessment = assess_success(selected_protocol, model_runs, analysis)
        self.assertFalse(assessment["passed"])
        self.assertIn(
            "maximum_dohaa_to_direct_token_ratio",
            assessment["unevaluable_criteria"],
        )

    def test_manifest_schema_and_example_share_root_fields(self):
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        example = json.loads(MANIFEST_EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(example))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(len(example["models"]), 3)
        with self.assertRaisesRegex(ModelManifestError, "placeholder"):
            ModelManifest.create(protocol(), example["models"])


if __name__ == "__main__":
    unittest.main()
