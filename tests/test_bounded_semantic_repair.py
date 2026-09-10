"""Fresh synthetic scheduling regressions; no model or closed-study fixtures."""

import unittest

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate, GateResult, ResultEqualsGate, SemanticAssertionsGate,
)
from hermes_dohaa.assurance.semantic_assertions import MAX_ASSERTIONS
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController, RunReasonCode, RunStatus
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_rule_aware_repair import RepairRuntime, assertion, contract_for, ref


def fixture(count=3, *, first_operator="equals", immutable_paths=None):
    facts = {f"field{i:02}": 13 + 17 * i for i in range(count)}
    rules = [
        assertion(
            f"fresh.{key}", first_operator if i == 0 else "equals",
            ref("result", "/" + key), ref("inputs", "/facts/" + key),
        )
        for i, key in enumerate(facts)
    ]
    return contract_for(rules, {"facts": facts}, immutable_paths=immutable_paths), facts


class BoundedSemanticRepairTests(unittest.TestCase):
    def run_fixture(self, contract, runtime, extra_gates=()):
        with EvidenceLedger(":memory:") as ledger:
            result = DohaaController(
                runtime, (SemanticAssertionsGate(), *extra_gates), ledger,
            ).run(contract)
            self.assertTrue(ledger.verify_chain())
            events = tuple(ledger.records(result.run_id))
        return result, events

    def test_disjoint_units_complete_with_one_runtime_attempt(self):
        for count in (2, 3, MAX_ASSERTIONS):
            with self.subTest(count=count):
                contract, facts = fixture(count)
                raw = contract.to_dict()
                raw["max_attempts"] = 1
                initial = Proposal(result=dict.fromkeys(facts, 0))
                runtime = RepairRuntime(initial, [])
                result, events = self.run_fixture(TaskContract.from_dict(raw), runtime)
                self.assertEqual(result.status, RunStatus.SUCCEEDED)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.proposal.result, facts)
                self.assertEqual(runtime.propose_calls, 1)
                self.assertEqual(runtime.repair_calls, [])
                self.assertEqual(initial.result, dict.fromkeys(facts, 0))
                applied = [e for e in events if e.event_type in (
                    "semantic.repair.applied", "semantic.repair.partially_applied",
                )]
                self.assertEqual(len(applied), count)
                previous = initial.fingerprint()
                for step, event in enumerate(applied, 1):
                    payload = event.payload
                    self.assertEqual(payload["deterministic_step"], step)
                    self.assertEqual(payload["baseline_fingerprint"], previous)
                    self.assertEqual(len(payload["repair_scope"]["failed_rule_ids"]), 1)
                    self.assertEqual(payload["change_assessment"]["outside_paths"], [])
                    self.assertEqual(payload["introduced_failures"], [])
                    self.assertTrue(payload["resolved_failures"])
                    previous = payload["candidate_fingerprint"]
                self.assertEqual(previous, result.proposal.fingerprint())

    def test_calculable_units_continue_after_valid_runtime_repair(self):
        contract, facts = fixture(first_operator="greater_than")
        initial = Proposal(result=dict.fromkeys(facts, 0))
        partial = {**initial.result, "field00": facts["field00"] + 1}
        runtime = RepairRuntime(initial, [Proposal(result=partial)])
        result, events = self.run_fixture(contract, runtime)
        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(runtime.repair_calls), 1)
        self.assertEqual(result.proposal.result, {**facts, "field00": partial["field00"]})
        adopted = next(i for i, e in enumerate(events) if e.event_type == "repair.candidate.adopted")
        self.assertTrue(all(i > adopted for i, e in enumerate(events)
                            if e.event_type.startswith("semantic.repair.")))

    def test_invalid_runtime_cannot_be_rescued_by_calculable_repairs(self):
        contract, facts = fixture(first_operator="greater_than")
        initial = Proposal(result=dict.fromkeys(facts, 0))
        for changes in (
            {"field00": facts["field00"] + 1, "field01": -999},
            {"field00": 1},
        ):
            with self.subTest(changes=changes):
                runtime = RepairRuntime(initial, [Proposal(result={**initial.result, **changes})])
                result, events = self.run_fixture(contract, runtime)
                self.assertEqual(result.status, RunStatus.ESCALATED)
                self.assertEqual(result.proposal, initial)
                self.assertTrue(any(e.event_type == "repair.candidate.rejected" for e in events))
                self.assertFalse(any(e.event_type.startswith("semantic.repair.") for e in events))

    def test_immutable_second_unit_stops_and_retains_first(self):
        contract, facts = fixture(immutable_paths=["/result/field01"])
        raw = contract.to_dict()
        raw["max_attempts"] = 1
        runtime = RepairRuntime(Proposal(result=dict.fromkeys(facts, 0)), [])
        result, events = self.run_fixture(TaskContract.from_dict(raw), runtime)
        self.assertEqual(result.reason_code, RunReasonCode.ATTEMPT_BUDGET_EXHAUSTED)
        self.assertEqual(result.proposal.result, {"field00": 13, "field01": 0, "field02": 0})
        self.assertEqual(sum(e.event_type == "semantic.repair.rejected" for e in events), 1)

    def test_second_unit_regression_rolls_back_and_does_not_loop(self):
        class PreserveSecondGate:
            name = "preserve_second"

            def evaluate(self, contract, proposal):
                passed = proposal.result["field01"] == 0
                return GateResult(self.name, passed, "Preserve second",
                                  failure_code=None if passed else "preserve.failed")

        contract, facts = fixture()
        raw = contract.to_dict()
        raw["max_attempts"] = 1
        runtime = RepairRuntime(Proposal(result=dict.fromkeys(facts, 0)), [])
        result, events = self.run_fixture(TaskContract.from_dict(raw), runtime, (PreserveSecondGate(),))
        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(result.proposal.result, {"field00": 13, "field01": 0, "field02": 0})
        self.assertEqual(sum(e.event_type == "semantic.repair.rejected" for e in events), 1)

    def test_hidden_oracle_never_authorizes_another_unit(self):
        contract, facts = fixture()
        runtime = RepairRuntime(Proposal(result=dict.fromkeys(facts, 0)), [])
        result, _ = self.run_fixture(contract, runtime, (ResultEqualsGate({"private": "sentinel"}),))
        self.assertEqual(result.reason_code, RunReasonCode.UNSIGNALED_FAILURE)
        self.assertEqual(result.proposal.result, facts)
        self.assertEqual(runtime.repair_calls, [])

    def test_unsignaled_policy_failure_prevents_deterministic_adoption(self):
        contract, facts = fixture()
        initial = Proposal(result=dict.fromkeys(facts, 0), requested_actions=("external.publish",))
        result, events = self.run_fixture(contract, RepairRuntime(initial, []), (ActionPolicyGate(),))
        self.assertEqual(result.reason_code, RunReasonCode.UNSIGNALED_FAILURE)
        self.assertEqual(result.proposal, initial)
        self.assertFalse(any(e.event_type.startswith("semantic.repair.") for e in events))

    def test_complete_chain_still_requires_human_approval(self):
        contract, facts = fixture()
        raw = contract.to_dict()
        raw["requires_human_approval"] = True
        runtime = RepairRuntime(Proposal(result=dict.fromkeys(facts, 0)), [])
        result, events = self.run_fixture(TaskContract.from_dict(raw), runtime)
        self.assertEqual(result.reason_code, RunReasonCode.HUMAN_APPROVAL_REQUIRED)
        self.assertEqual(result.proposal.result, facts)
        self.assertTrue(any(e.event_type == "run.checkpointed" for e in events))


if __name__ == "__main__":
    unittest.main()
