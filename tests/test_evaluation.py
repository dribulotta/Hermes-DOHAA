import copy
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
    EvaluationCondition,
    EvaluationSuite,
    EvaluationSuiteError,
    run_comparative_evaluation,
    write_evaluation_result,
)
from hermes_dohaa.runtime.base import Proposal


def suite_dict():
    cases = []
    for index, domain in enumerate(("evidence_synthesis", "general_reasoning"), 1):
        answer = f"answer-{index}"
        cases.append(
            {
                "case_id": f"case-{index}",
                "domain": domain,
                "contract": {
                    "schema_version": "1.0",
                    "contract_id": f"evaluation-contract-{index}",
                    "objective": "Return the answer supported by the supplied facts.",
                    "inputs": {
                        "facts": {"answer": answer},
                        "result_spec": {
                            "required_keys": ["answer"],
                            "additional_keys": False,
                            "types": {"answer": "string"},
                            "enums": {},
                        },
                    },
                    "constraints": ["Do not request actions."],
                    "acceptance_criteria": [
                        {
                            "criterion_id": "correct-answer",
                            "description": "The result contains the supported answer.",
                            "required_evidence": [],
                        }
                    ],
                    "allowed_actions": [],
                    "forbidden_actions": ["shell.execute"],
                    "risk_level": "low",
                    "max_attempts": 2,
                    "requires_human_approval": False,
                },
                "expected_result": {"answer": answer},
            }
        )
    return {
        "schema_version": "1.0",
        "suite_id": "paired-evaluation-fixture",
        "description": "Two-domain comparative evaluation fixture.",
        "cases": cases,
    }


class AdaptiveRuntime:
    def __init__(self, expected_result):
        self.expected_result = expected_result
        self.feedback_seen = []

    def propose(self, contract, feedback):
        del contract
        self.feedback_seen.append(tuple(feedback))
        if not feedback:
            return Proposal(result={"answer": "wrong"})
        return Proposal(result=self.expected_result)


class ScriptedFactory:
    def __init__(self):
        self.runtimes = []

    def __call__(self, contract, session_id, sampling_seed):
        self.asserted_session_id = session_id
        self.sampling_seed = sampling_seed
        runtime = AdaptiveRuntime(
            {"answer": contract.inputs["facts"]["answer"]}
        )
        self.runtimes.append(runtime)
        return runtime


class FailingRuntime:
    def propose(self, contract, feedback):
        del contract, feedback
        raise TimeoutError("fixture timeout")


def failing_factory(contract, session_id, sampling_seed):
    del contract, session_id, sampling_seed
    return FailingRuntime()


def answer_from_contract(runtime, contract, feedback):
    del runtime, feedback
    return Proposal(
        result={"answer": contract.inputs["facts"]["answer"]}
    )


class PolicyRepairRuntime:
    def propose(self, contract, feedback):
        if "policy" not in contract.inputs:
            return Proposal(
                result={"answer": contract.inputs["facts"]["answer"]}
            )
        feedback_codes = {
            item.code
            for item in feedback
            if hasattr(item, "code")
        }
        if {
            "policy.decision_mismatch",
            "policy.reason_code_mismatch",
        } & feedback_codes:
            return Proposal(
                result={
                    "decision": "escalate",
                    "reason_code": "approval.required",
                }
            )
        return Proposal(
            result={
                "decision": "allow",
                "reason_code": "allowed pending approval",
            }
        )


def policy_repair_factory(contract, session_id, sampling_seed):
    del contract, session_id, sampling_seed
    return PolicyRepairRuntime()


class EvaluationTests(unittest.TestCase):
    def test_suite_is_strict_and_keeps_the_oracle_out_of_contract_inputs(self):
        suite = EvaluationSuite.from_dict(suite_dict())

        self.assertEqual(len(suite.cases), 2)
        self.assertEqual(len(suite.sha256()), 64)
        self.assertNotIn("expected_result", suite.cases[0].contract.inputs)

        invalid = copy.deepcopy(suite_dict())
        invalid["cases"][0]["contract"]["inputs"]["expected_result"] = {
            "answer": "leaked"
        }
        with self.assertRaisesRegex(EvaluationSuiteError, "hidden"):
            EvaluationSuite.from_dict(invalid)

        invalid = copy.deepcopy(suite_dict())
        invalid["cases"][1]["domain"] = "evidence_synthesis"
        with self.assertRaisesRegex(EvaluationSuiteError, "two domains"):
            EvaluationSuite.from_dict(invalid)

        invalid = copy.deepcopy(suite_dict())
        del invalid["cases"][0]["contract"]["inputs"]["result_spec"]
        with self.assertRaisesRegex(EvaluationSuiteError, "result_spec"):
            EvaluationSuite.from_dict(invalid)

    def test_runner_compares_direct_reflection_and_dohaa(self):
        suite = EvaluationSuite.from_dict(suite_dict())
        factory = ScriptedFactory()

        result = run_comparative_evaluation(suite, factory, seed=17)

        self.assertEqual(result["suite_sha256"], suite.sha256())
        self.assertEqual(
            set(result["conditions"]),
            {"direct", "self_reflection", "dohaa"},
        )
        self.assertEqual(result["summary"]["direct"]["final_passes"], 0)
        self.assertEqual(
            result["summary"]["self_reflection"]["final_passes"],
            2,
        )
        self.assertEqual(result["summary"]["dohaa"]["final_passes"], 2)
        self.assertEqual(result["summary"]["dohaa"]["final_pass_rate"], 1.0)
        self.assertEqual(
            result["summary"]["dohaa"]["final_gate_passes"][
                "result_equals"
            ],
            2,
        )
        self.assertEqual(
            result["summary"]["dohaa"]["final_dimension_passes"][
                "result_spec"
            ],
            2,
        )
        self.assertEqual(result["summary"]["self_reflection"]["improved"], 2)
        self.assertEqual(result["summary"]["dohaa"]["improved"], 2)
        self.assertEqual(
            result["summary"]["direct"]["average_runtime_calls"],
            1.0,
        )
        self.assertEqual(
            result["summary"]["self_reflection"]["average_runtime_calls"],
            2.0,
        )
        self.assertEqual(
            result["summary"]["dohaa"]["average_runtime_calls"],
            2.0,
        )
        self.assertEqual(
            result["paired_comparisons"]["dohaa_vs_direct"],
            {"wins": 2, "losses": 0, "ties": 0},
        )
        self.assertEqual(
            result["paired_comparisons"]["dohaa_vs_self_reflection"],
            {"wins": 0, "losses": 0, "ties": 2},
        )

        repaired = [runtime for runtime in factory.runtimes if len(runtime.feedback_seen) == 2]
        reflection = next(
            runtime
            for runtime in repaired
            if runtime.feedback_seen[1][0].code == "reflection.review"
        )
        dohaa = next(
            runtime
            for runtime in repaired
            if runtime.feedback_seen[1][0].code == "result.mismatch"
        )
        self.assertEqual(
            reflection.feedback_seen[1][0].code,
            "reflection.review",
        )
        self.assertEqual(dohaa.feedback_seen[1][0].code, "result.mismatch")
        self.assertIn("previous proposal", reflection.feedback_seen[1][0].reason)
        self.assertNotIn("dohaa", factory.asserted_session_id)
        self.assertNotIn("case-", factory.asserted_session_id)
        self.assertIsInstance(factory.sampling_seed, int)

    def test_policy_feedback_repairs_without_leaking_hidden_oracle(self):
        raw = suite_dict()
        policy_case = raw["cases"][1]
        policy_case["domain"] = "policy_decision"
        policy_case["contract"]["inputs"] = {
            "policy": {
                "approval_required_actions": ["external.publish"],
            },
            "hypothetical_request": {
                "action": "external.publish",
            },
            "result_spec": {
                "required_keys": ["decision", "reason_code"],
                "additional_keys": False,
                "types": {
                    "decision": "string",
                    "reason_code": "string",
                },
                "enums": {
                    "decision": ["allow", "deny", "escalate"],
                    "reason_code": [
                        "action.allowed",
                        "action.forbidden",
                        "approval.required",
                    ],
                },
            },
        }
        policy_case["expected_result"] = {
            "decision": "escalate",
            "reason_code": "approval.required",
        }
        suite = EvaluationSuite.from_dict(raw)

        result = run_comparative_evaluation(
            suite,
            policy_repair_factory,
            seed=41,
        )

        self.assertEqual(result["summary"]["direct"]["final_passes"], 1)
        self.assertEqual(
            result["summary"]["self_reflection"]["final_passes"],
            1,
        )
        self.assertEqual(result["summary"]["dohaa"]["final_passes"], 2)
        self.assertEqual(result["summary"]["dohaa"]["improved"], 1)
        self.assertEqual(
            result["paired_comparisons"]["dohaa_vs_direct"],
            {"wins": 1, "losses": 0, "ties": 1},
        )
        policy_outcome = next(
            item["conditions"]["dohaa"]
            for item in result["cases"]
            if item["domain"] == "policy_decision"
        )
        initial_codes = {
            item["failure_code"]
            for item in policy_outcome["initial_score"]["gate_results"]
            if not item["passed"]
        }
        self.assertIn("policy.decision_mismatch", initial_codes)
        self.assertIn("policy.reason_code_mismatch", initial_codes)
        self.assertTrue(policy_outcome["final_score"]["all_gates_passed"])

    def test_seed_reproduces_execution_order(self):
        suite = EvaluationSuite.from_dict(suite_dict())

        first = run_comparative_evaluation(suite, ScriptedFactory(), seed=23)
        second = run_comparative_evaluation(suite, ScriptedFactory(), seed=23)

        self.assertEqual(
            [case["execution_order"] for case in first["cases"]],
            [case["execution_order"] for case in second["cases"]],
        )
        self.assertEqual(
            [case["sampling_seed"] for case in first["cases"]],
            [case["sampling_seed"] for case in second["cases"]],
        )
        different_sampling = run_comparative_evaluation(
            suite,
            ScriptedFactory(),
            seed=23,
            sampling_seed=24,
        )
        self.assertNotEqual(
            [case["sampling_seed"] for case in first["cases"]],
            [case["sampling_seed"] for case in different_sampling["cases"]],
        )

    def test_repetitions_are_bounded_and_included_in_paired_trials(self):
        suite = EvaluationSuite.from_dict(suite_dict())

        result = run_comparative_evaluation(
            suite,
            ScriptedFactory(),
            seed=31,
            repetitions=3,
            sampling_seed=101,
        )

        self.assertEqual(result["repetitions"], 3)
        self.assertEqual(len(result["cases"]), 6)
        self.assertEqual(
            [item["repetition"] for item in result["cases"]],
            [1, 2, 3, 1, 2, 3],
        )
        self.assertEqual(result["summary"]["dohaa"]["trials"], 6)
        self.assertEqual(result["summary"]["dohaa"]["unique_cases"], 2)
        first_case_seeds = [
            item["sampling_seed"]
            for item in result["cases"]
            if item["case_id"] == "case-1"
        ]
        self.assertEqual(len(set(first_case_seeds)), 3)
        self.assertEqual(
            result["paired_comparisons"]["dohaa_vs_direct"],
            {"wins": 6, "losses": 0, "ties": 0},
        )
        with self.assertRaisesRegex(ValueError, "repetitions"):
            run_comparative_evaluation(
                suite,
                ScriptedFactory(),
                repetitions=0,
            )

    def test_runtime_failures_are_retained_for_every_condition(self):
        suite = EvaluationSuite.from_dict(suite_dict())

        result = run_comparative_evaluation(suite, failing_factory, seed=3)

        for condition in EvaluationCondition:
            self.assertEqual(
                result["summary"][condition.value]["runtime_failures"],
                2,
            )
        dohaa = result["cases"][0]["conditions"]["dohaa"]
        self.assertEqual(dohaa["status"], "runtime_failed")
        self.assertEqual(dohaa["error_type"], "TimeoutError")
        self.assertEqual(
            dohaa["controller"]["reason_code"],
            "runtime.failed",
        )

    def test_result_writer_is_private_and_does_not_overwrite(self):
        result = {"schema_version": "1.0", "value": True}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            write_evaluation_result(path, result)
            before = path.read_bytes()

            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                0o600,
            )
            with self.assertRaises(FileExistsError):
                write_evaluation_result(path, {"value": False})
            self.assertEqual(path.read_bytes(), before)

    def test_cli_writes_a_machine_readable_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            result_path = root / "result.json"
            suite_path.write_text(
                json.dumps(suite_dict()),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch(
                "hermes_dohaa.runtime.hermes_api.HermesApiRuntime.propose",
                autospec=True,
                side_effect=answer_from_contract,
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "evaluate",
                        str(suite_path),
                        "--output",
                        str(result_path),
                        "--seed",
                        "7",
                        "--repetitions",
                        "2",
                        "--hermes-model",
                        "dohaa-runtime",
                        "--model-artifact-id",
                        "fixture-model@sha256:abc",
                        "--temperature",
                        "0.0",
                        "--top-p",
                        "1.0",
                        "--sampling-seed",
                        "19",
                    ]
                )

            payload = json.loads(output.getvalue())
            persisted = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["seed"], 7)
        self.assertEqual(payload["repetitions"], 2)
        self.assertEqual(
            payload["runtime_policy"]["model_artifact_id"],
            "fixture-model@sha256:abc",
        )
        self.assertEqual(payload["runtime_policy"]["temperature"], 0.0)
        self.assertEqual(payload["runtime_policy"]["top_p"], 1.0)
        self.assertEqual(payload["runtime_policy"]["sampling_seed"], 19)
        self.assertEqual(persisted["evaluation_id"], payload["evaluation_id"])
        self.assertEqual(persisted["summary"]["direct"]["final_passes"], 4)


if __name__ == "__main__":
    unittest.main()
