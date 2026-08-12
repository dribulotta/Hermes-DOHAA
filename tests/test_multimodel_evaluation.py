import io
import json
import stat
import tempfile
import unittest
import copy
import hashlib
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
    aggregate_model_slot_checkpoints,
    analyze_multimodel_results,
    assess_success,
    run_multimodel_evaluation,
    run_model_slot_evaluation,
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


class IsolatedModelSlotTests(unittest.TestCase):
    COMMIT = "d" * 40

    def setUp(self):
        self.protocol = protocol()
        self.manifest = ModelManifest.create(self.protocol, model_dicts())
        self.suite = protected_suite()
        self.commitment = SuiteCommitment.create(
            self.suite, protocol_commit="e" * 40
        )

    def checkpoint(self, slot_id, *, built=None):
        def builder(model):
            if built is not None:
                built.append(model.model_alias)
            return lambda contract, session_id, seed: ExactRuntime()
        return run_model_slot_evaluation(
            self.suite, self.commitment, self.protocol, self.manifest,
            slot_id, builder, runtime_context={"adapter": "synthetic"},
            execution_code_commit=self.COMMIT,
        )

    def checkpoints(self):
        return [self.checkpoint(slot.slot_id) for slot in self.protocol.model_slots]

    def test_slot_builds_and_executes_only_selected_alias(self):
        built = []
        selected = self.manifest.models[1]
        checkpoint = self.checkpoint(selected.slot_id, built=built)
        self.assertEqual(built, [selected.model_alias])
        self.assertEqual(checkpoint["model_alias"], selected.model_alias)
        self.assertEqual(checkpoint["evaluation"]["runtime_policy"]["model_alias"],
                         selected.model_alias)

    def test_unknown_slot_and_invalid_commit_fail_before_builder(self):
        called = []
        def builder(model):
            called.append(model)
            raise AssertionError("must not build")
        with self.assertRaisesRegex(MultimodelEvaluationError, "unknown"):
            run_model_slot_evaluation(
                self.suite, self.commitment, self.protocol, self.manifest,
                "absent", builder, execution_code_commit=self.COMMIT,
            )
        with self.assertRaisesRegex(MultimodelEvaluationError, "40-character"):
            run_model_slot_evaluation(
                self.suite, self.commitment, self.protocol, self.manifest,
                self.manifest.models[0].slot_id, builder,
                execution_code_commit="ABC123",
            )
        self.assertEqual(called, [])

    def test_checkpoint_round_trip_and_mutable_values_are_detached(self):
        context = {"nested": {"items": [1]}}
        checkpoint = run_model_slot_evaluation(
            self.suite, self.commitment, self.protocol, self.manifest,
            self.manifest.models[0].slot_id,
            lambda model: (lambda contract, session_id, seed: ExactRuntime()),
            runtime_context=context, execution_code_commit=self.COMMIT,
        )
        context["nested"]["items"].append(2)
        self.assertEqual(checkpoint["runtime_policy"]["nested"]["items"], [1])
        self.assertEqual(json.loads(json.dumps(checkpoint)), checkpoint)

    def test_three_checkpoints_aggregate_in_protocol_order_without_runtime(self):
        checkpoints = self.checkpoints()
        result = aggregate_model_slot_checkpoints(
            self.suite, self.commitment, self.protocol, self.manifest,
            checkpoints, execution_code_commit=self.COMMIT,
        )
        self.assertEqual([run["slot_id"] for run in result["model_runs"]],
                         [slot.slot_id for slot in self.protocol.model_slots])
        self.assertEqual(len(result["source_checkpoints"]), 3)
        self.assertTrue(all(len(source["checkpoint_sha256"]) == 64
                            for source in result["source_checkpoints"]))

    def test_missing_extra_duplicate_and_disordered_slots_are_rejected(self):
        checkpoints = self.checkpoints()
        variants = [
            checkpoints[:-1],
            checkpoints + [checkpoints[0]],
            [checkpoints[0], checkpoints[0], checkpoints[2]],
            [checkpoints[1], checkpoints[0], checkpoints[2]],
        ]
        for value in variants:
            with self.subTest(slots=[item["slot_id"] for item in value]):
                with self.assertRaises(MultimodelEvaluationError):
                    aggregate_model_slot_checkpoints(
                        self.suite, self.commitment, self.protocol,
                        self.manifest, value,
                        execution_code_commit=self.COMMIT,
                    )

    def test_tampered_checkpoint_fields_are_rejected(self):
        checkpoints = self.checkpoints()
        mutations = {
            "protocol_sha256": "0" * 64,
            "model_manifest_sha256": "0" * 64,
            "suite_sha256": "0" * 64,
            "suite_commitment_sha256": "0" * 64,
            "model_alias": "tampered-alias",
            "model_artifact_id": "tampered-artifact",
            "execution_code_commit": "f" * 40,
            "status": "incomplete",
            "schema_version": "2.0",
            "checkpoint_type": "other",
        }
        for field, value in mutations.items():
            altered = copy.deepcopy(checkpoints)
            altered[0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(MultimodelEvaluationError):
                    aggregate_model_slot_checkpoints(
                        self.suite, self.commitment, self.protocol,
                        self.manifest, altered,
                        execution_code_commit=self.COMMIT,
                    )
        altered = copy.deepcopy(checkpoints)
        altered[0]["runtime_policy"]["temperature"] = 2.0
        with self.assertRaises(MultimodelEvaluationError):
            aggregate_model_slot_checkpoints(
                self.suite, self.commitment, self.protocol, self.manifest,
                altered, execution_code_commit=self.COMMIT,
            )

    def test_evaluation_runtime_policy_tampering_is_rejected_with_new_id(self):
        checkpoints = self.checkpoints()
        altered = copy.deepcopy(checkpoints)
        altered[0]["evaluation"]["runtime_policy"]["hermes_url"] = (
            "http://synthetic.invalid/v1"
        )
        identity_value = {
            key: value for key, value in altered[0].items()
            if key != "checkpoint_id"
        }
        canonical = json.dumps(
            identity_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        altered[0]["checkpoint_id"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(
            MultimodelEvaluationError, "evaluation.runtime_policy"
        ):
            aggregate_model_slot_checkpoints(
                self.suite, self.commitment, self.protocol, self.manifest,
                altered, execution_code_commit=self.COMMIT,
            )

    def test_cli_slot_preserves_url_and_matches_monolithic_runtime_policy(self):
        endpoint = "http://synthetic.invalid:8642/v1"

        def exact_proposal(runtime, contract, feedback):
            del feedback
            runtime.usage_records.append({"total_tokens": 10})
            return Proposal(dict(contract.inputs["answer"]))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            commitment_path = root / "suite.commitment.json"
            manifest_path = root / "models.json"
            checkpoint_path = root / "slot.json"
            monolithic_path = root / "monolithic.json"
            suite_path.write_text(
                json.dumps(self.suite.to_dict()), encoding="utf-8"
            )
            write_suite_commitment(commitment_path, self.commitment)
            write_model_manifest(manifest_path, self.manifest)
            slot = self.manifest.models[1]
            common = [
                str(suite_path),
                "--suite-commitment", str(commitment_path),
                "--protocol", str(PROTOCOL_PATH),
                "--model-manifest", str(manifest_path),
                "--hermes-url", endpoint,
            ]
            with patch.object(
                HermesApiRuntime,
                "propose",
                autospec=True,
                side_effect=exact_proposal,
            ), redirect_stdout(io.StringIO()):
                slot_exit = main([
                    "evaluate-model-slot", *common,
                    "--slot-id", slot.slot_id,
                    "--execution-code-commit", self.COMMIT,
                    "--output", str(checkpoint_path),
                ])
                monolithic_exit = main([
                    "evaluate-multimodel", *common,
                    "--output", str(monolithic_path),
                ])

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            monolithic = json.loads(monolithic_path.read_text(encoding="utf-8"))
            monolithic_run = next(
                run for run in monolithic["model_runs"]
                if run["slot_id"] == slot.slot_id
            )
            self.assertEqual(slot_exit, 0)
            self.assertEqual(monolithic_exit, 0)
            self.assertEqual(checkpoint["runtime_policy"]["hermes_url"], endpoint)
            self.assertEqual(
                checkpoint["evaluation"]["runtime_policy"]["hermes_url"],
                endpoint,
            )
            self.assertEqual(
                checkpoint["runtime_policy"],
                monolithic_run["evaluation"]["runtime_policy"],
            )

    def test_checkpoint_aggregation_matches_monolithic_results(self):
        def builder(model):
            return lambda contract, session_id, seed: ExactRuntime()
        def deterministic(suite, factory, **kwargs):
            del suite, factory
            evaluation = model_run("unused", [True] * 48, [True] * 48)[
                "evaluation"
            ]
            evaluation["runtime_policy"] = copy.deepcopy(kwargs["runtime_policy"])
            return evaluation
        with patch(
            "hermes_dohaa.evaluation.multimodel.run_comparative_evaluation",
            side_effect=deterministic,
        ):
            monolithic = run_multimodel_evaluation(
                self.suite, self.commitment, self.protocol, self.manifest,
                builder, runtime_context={"adapter": "synthetic"},
            )
            isolated = aggregate_model_slot_checkpoints(
                self.suite, self.commitment, self.protocol, self.manifest,
                self.checkpoints(), execution_code_commit=self.COMMIT,
            )
        self.assertEqual(isolated["model_runs"], monolithic["model_runs"])
        self.assertEqual(isolated["aggregate_analysis"],
                         monolithic["aggregate_analysis"])
        self.assertEqual(isolated["success_assessment"],
                         monolithic["success_assessment"])

    def test_private_checkpoint_writer_rejects_overwrite(self):
        checkpoint = self.checkpoint(self.manifest.models[0].slot_id)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            from hermes_dohaa.evaluation import write_evaluation_result
            write_evaluation_result(path, checkpoint)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                write_evaluation_result(path, checkpoint)


if __name__ == "__main__":
    unittest.main()
