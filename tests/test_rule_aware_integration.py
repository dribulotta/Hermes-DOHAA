import json
import unittest

from hermes_dohaa.assurance.gates import SemanticAssertionsGate
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController, RunStatus
from hermes_dohaa.evaluation import EvaluationSuite, run_comparative_evaluation
from hermes_dohaa.evaluation.runner import _ObservedRuntime
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_contracts import valid_contract


def ref(source, pointer):
    return {"op": "ref", "source": source, "pointer": pointer}


def minimum_assertion():
    return {
        "assertion_id": "dev.minimum",
        "operator": "greater_than",
        "left": ref("result", "/score"),
        "right": ref("inputs", "/minimum"),
    }


def result_spec():
    return {
        "required_keys": ["score"],
        "additional_keys": False,
        "types": {"score": "integer"},
        "enums": {},
    }


def rule_aware_policy():
    return {
        "schema_version": "1.0",
        "mode": "rule_aware",
        "preserve_unlisted": True,
        "require_strict_improvement": True,
    }


class RepairCapableLegacyRuntime:
    def __init__(self):
        self.propose_calls = 0
        self.repair_calls = 0
        self.feedback_seen = []

    def propose(self, contract, feedback):
        del contract
        self.propose_calls += 1
        self.feedback_seen.append(tuple(feedback))
        score = 1 if self.propose_calls == 1 else 11
        return Proposal(result={"score": score})

    def repair(self, contract, baseline, feedback, repair_scope):
        del contract, baseline, feedback, repair_scope
        self.repair_calls += 1
        raise AssertionError("legacy contracts must not call repair()")


class EvaluationRepairRuntime:
    def __init__(self):
        self.propose_calls = 0
        self.repair_calls = []
        self.initial = None

    def propose(self, contract, feedback):
        del contract, feedback
        self.propose_calls += 1
        self.initial = Proposal(result={"score": 1})
        return self.initial

    def repair(self, contract, baseline, feedback, repair_scope):
        del contract
        self.initial.result["score"] = 99
        self.repair_calls.append(
            {
                "baseline": baseline,
                "feedback": tuple(feedback),
                "repair_scope": repair_scope,
            }
        )
        return Proposal(result={"score": 11})


class EvaluationRepairFactory:
    def __init__(self):
        self.runtimes = []

    def __call__(self, contract, session_id, sampling_seed):
        del contract, session_id, sampling_seed
        runtime = EvaluationRepairRuntime()
        self.runtimes.append(runtime)
        return runtime


def rule_aware_suite():
    cases = []
    for index, domain in enumerate(
        ("arithmetic_development", "calendar_development"),
        1,
    ):
        cases.append(
            {
                "case_id": f"rule-aware-case-{index}",
                "domain": domain,
                "contract": valid_contract(
                    contract_id=f"rule-aware-contract-{index}",
                    objective="Return a score above the visible minimum.",
                    inputs={
                        "minimum": 10,
                        "result_spec": result_spec(),
                        "semantic_assertions": [minimum_assertion()],
                        "repair_policy": rule_aware_policy(),
                    },
                    acceptance_criteria=[
                        {
                            "criterion_id": "development-only",
                            "description": "Exercise scoped evaluation repair.",
                            "required_evidence": [],
                        }
                    ],
                    allowed_actions=[],
                    forbidden_actions=["shell.execute"],
                    max_attempts=2,
                ),
                "expected_result": {"score": 11},
            }
        )
    return EvaluationSuite.from_dict(
        {
            "schema_version": "1.0",
            "suite_id": "rule-aware-runner-development",
            "description": "Public synthetic rule-aware runner integration.",
            "cases": cases,
        }
    )


class RuleAwareIntegrationTests(unittest.TestCase):
    def test_observer_snapshots_initial_proposal_before_reflection(self):
        class MutatingHistoryRuntime:
            def __init__(self):
                self.calls = 0
                self.first = None

            def propose(self, contract, feedback):
                del contract, feedback
                self.calls += 1
                if self.calls == 1:
                    self.first = Proposal(result={"score": 1})
                    return self.first
                self.first.result["score"] = 99
                return Proposal(result={"score": 11})

        observed = _ObservedRuntime(MutatingHistoryRuntime())
        contract = rule_aware_suite().cases[0].contract
        observed.propose(contract, ())
        observed.propose(contract, ())

        self.assertEqual(observed.proposals[0].result, {"score": 1})
        self.assertEqual(observed.proposals[1].result, {"score": 11})

    def test_conditions_receive_isolated_contract_snapshots(self):
        raw = rule_aware_suite().to_dict()
        for case in raw["cases"]:
            inputs = case["contract"]["inputs"]
            del inputs["repair_policy"]
            inputs["config"] = {"minimum": inputs.pop("minimum")}
            inputs["semantic_assertions"][0]["right"][
                "pointer"
            ] = "/config/minimum"
        suite = EvaluationSuite.from_dict(raw)
        factory_seen = []
        runtime_seen = []

        class MutatingRuntime:
            def propose(self, contract, feedback):
                del feedback
                runtime_seen.append(contract.inputs["config"]["minimum"])
                contract.inputs["config"]["minimum"] = -200
                return Proposal(result={"score": 1})

        def factory(contract, session_id, sampling_seed):
            del session_id, sampling_seed
            factory_seen.append(contract.inputs["config"]["minimum"])
            contract.inputs["config"]["minimum"] = -100
            return MutatingRuntime()

        result = run_comparative_evaluation(suite, factory, seed=17)

        self.assertEqual(factory_seen, [10] * 6)
        self.assertEqual(runtime_seen, [10] * 10)
        self.assertTrue(
            all(
                case.contract.inputs["config"]["minimum"] == 10
                for case in suite.cases
            )
        )
        self.assertEqual(result["summary"]["direct"]["final_passes"], 0)

    def test_legacy_contract_uses_propose_and_legacy_feedback(self):
        contract = TaskContract.from_dict(
            valid_contract(
                inputs={
                    "minimum": 10,
                    "semantic_assertions": [minimum_assertion()],
                },
                max_attempts=2,
                allowed_actions=[],
            )
        )
        runtime = RepairCapableLegacyRuntime()

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(runtime.propose_calls, 2)
        self.assertEqual(runtime.repair_calls, 0)
        self.assertEqual(len(runtime.feedback_seen), 2)
        retry_feedback = runtime.feedback_seen[1]
        self.assertEqual(len(retry_feedback), 1)
        self.assertEqual(
            retry_feedback[0].code,
            "semantic.assertion_failed",
        )
        self.assertNotIn("repair_scope", retry_feedback[0].details)

    def test_rule_aware_contract_requires_repair_capability(self):
        class ProposeOnlyRuntime:
            def __init__(self):
                self.calls = 0

            def propose(self, contract, feedback):
                del contract, feedback
                self.calls += 1
                return Proposal(result={"score": 1})

        runtime = ProposeOnlyRuntime()
        contract = rule_aware_suite().cases[0].contract

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(runtime.calls, 1)
        self.assertEqual(result.status.value, "escalated")
        self.assertEqual(
            result.reason_code.value,
            "repair.runtime_unavailable",
        )

        evaluation = run_comparative_evaluation(
            rule_aware_suite(),
            lambda contract, session_id, sampling_seed: ProposeOnlyRuntime(),
            seed=23,
        )
        for case in evaluation["cases"]:
            outcome = case["conditions"]["dohaa"]
            self.assertEqual(outcome["runtime_calls"], 1)
            self.assertEqual(outcome["status"], "runtime_failed")
            self.assertEqual(
                outcome["error_code"],
                "repair.runtime_unavailable",
            )
            self.assertEqual(
                outcome["controller"]["reason_code"],
                "repair.runtime_unavailable",
            )
        self.assertEqual(
            evaluation["summary"]["dohaa"]["runtime_failures"],
            2,
        )
        self.assertEqual(evaluation["summary"]["dohaa"]["completed"], 0)

    def test_evaluation_runner_observes_rule_aware_repair(self):
        factory = EvaluationRepairFactory()

        result = run_comparative_evaluation(
            rule_aware_suite(),
            factory,
            seed=29,
        )

        self.assertEqual(result["summary"]["direct"]["final_passes"], 0)
        self.assertEqual(
            result["summary"]["self_reflection"]["final_passes"],
            0,
        )
        self.assertEqual(result["summary"]["dohaa"]["final_passes"], 2)
        self.assertEqual(result["summary"]["dohaa"]["initial_passes"], 0)

        for case in result["cases"]:
            outcome = case["conditions"]["dohaa"]
            self.assertEqual(outcome["runtime_calls"], 2)
            self.assertEqual(outcome["repair_transition"], "repaired")
            self.assertEqual(
                outcome["controller"]["reason_code"],
                "run.succeeded",
            )

        repaired_runtimes = [
            runtime for runtime in factory.runtimes if runtime.repair_calls
        ]
        self.assertEqual(len(repaired_runtimes), 2)
        for runtime in repaired_runtimes:
            self.assertEqual(runtime.propose_calls, 1)
            self.assertEqual(len(runtime.repair_calls), 1)
            call = runtime.repair_calls[0]
            self.assertEqual(call["baseline"].result, {"score": 1})
            self.assertEqual(
                call["repair_scope"]["editable_paths"],
                ["/result/score"],
            )
            self.assertEqual(
                call["repair_scope"]["source_pointers"],
                [
                    {
                        "source": "contract.inputs",
                        "pointer": "/minimum",
                    }
                ],
            )
            self.assertEqual(call["feedback"][0].code, "repair.scoped_retry")
            serialized = json.dumps(
                [item.to_dict() for item in call["feedback"]]
            )
            self.assertNotIn("expected_value", serialized)
            self.assertNotIn("actual_value", serialized)


if __name__ == "__main__":
    unittest.main()
