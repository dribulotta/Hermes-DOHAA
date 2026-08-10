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
    for index, domain in enumerate(("evidence_synthesis", "policy_decision"), 1):
        answer = f"answer-{index}"
        cases.append(
            {
                "case_id": f"case-{index}",
                "domain": domain,
                "contract": {
                    "schema_version": "1.0",
                    "contract_id": f"evaluation-contract-{index}",
                    "objective": "Return the answer supported by the supplied facts.",
                    "inputs": {"facts": {"answer": answer}},
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

    def __call__(self, contract, session_id):
        self.asserted_session_id = session_id
        runtime = AdaptiveRuntime(
            {"answer": contract.inputs["facts"]["answer"]}
        )
        self.runtimes.append(runtime)
        return runtime


class FailingRuntime:
    def propose(self, contract, feedback):
        del contract, feedback
        raise TimeoutError("fixture timeout")


def failing_factory(contract, session_id):
    del contract, session_id
    return FailingRuntime()


def answer_from_contract(runtime, contract, feedback):
    del runtime, feedback
    return Proposal(
        result={"answer": contract.inputs["facts"]["answer"]}
    )


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

    def test_seed_reproduces_execution_order(self):
        suite = EvaluationSuite.from_dict(suite_dict())

        first = run_comparative_evaluation(suite, ScriptedFactory(), seed=23)
        second = run_comparative_evaluation(suite, ScriptedFactory(), seed=23)

        self.assertEqual(
            [case["execution_order"] for case in first["cases"]],
            [case["execution_order"] for case in second["cases"]],
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
                        "--hermes-model",
                        "dohaa-runtime",
                        "--model-artifact-id",
                        "fixture-model@sha256:abc",
                    ]
                )

            payload = json.loads(output.getvalue())
            persisted = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["seed"], 7)
        self.assertEqual(
            payload["runtime_policy"]["model_artifact_id"],
            "fixture-model@sha256:abc",
        )
        self.assertEqual(persisted["evaluation_id"], payload["evaluation_id"])
        self.assertEqual(persisted["summary"]["direct"]["final_passes"], 2)


if __name__ == "__main__":
    unittest.main()
