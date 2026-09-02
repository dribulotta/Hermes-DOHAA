import json
import unittest

from hermes_dohaa.assurance.gates import (
    GateResult,
    ResultEqualsGate,
    SemanticAssertionsGate,
)
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import (
    DohaaController,
    RunReasonCode,
    RunStatus,
)
from hermes_dohaa.controller.repair_policy import (
    RepairScope,
    RuleAwareRepairPolicy,
    assess_candidate_changes,
    compare_failure_sets,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_contracts import valid_contract


def ref(source, pointer):
    return {"op": "ref", "source": source, "pointer": pointer}


def expression(op, *args):
    return {"op": op, "args": list(args)}


def assertion(
    assertion_id,
    operator,
    left,
    right,
    *,
    description=None,
    repair_group=None,
):
    value = {
        "assertion_id": assertion_id,
        "operator": operator,
        "left": left,
        "right": right,
    }
    if description is not None:
        value["description"] = description
    if repair_group is not None:
        value["repair_group"] = repair_group
    return value


def repair_policy(*, immutable_paths=None):
    value = {
        "schema_version": "1.0",
        "mode": "rule_aware",
        "preserve_unlisted": True,
        "require_strict_improvement": True,
    }
    if immutable_paths is not None:
        value["immutable_paths"] = immutable_paths
    return value


def contract_for(
    assertions,
    inputs,
    *,
    max_attempts=2,
    immutable_paths=None,
):
    return TaskContract.from_dict(
        valid_contract(
            inputs={
                **inputs,
                "repair_policy": repair_policy(
                    immutable_paths=immutable_paths
                ),
                "semantic_assertions": assertions,
            },
            max_attempts=max_attempts,
            allowed_actions=[],
            acceptance_criteria=[
                {
                    "criterion_id": "development-only",
                    "description": "Exercise public synthetic repair invariants.",
                    "required_evidence": [],
                }
            ],
        )
    )


class RepairRuntime:
    def __init__(self, initial, repairs):
        self.initial = initial
        self.repairs = iter(repairs)
        self.propose_calls = 0
        self.repair_calls = []

    def propose(self, contract, feedback):
        del contract
        self.propose_calls += 1
        if feedback:
            return next(self.repairs)
        return self.initial

    def repair(self, contract, baseline, feedback, repair_scope):
        del contract
        self.repair_calls.append((baseline, tuple(feedback), repair_scope))
        return next(self.repairs)


class RuleAwareRepairTests(unittest.TestCase):
    def test_semantic_scope_is_value_free_and_expands_atomic_group(self):
        sentinel = "DEV-CORRECT-VALUE-MUST-NOT-ENTER-FEEDBACK"
        rules = [
            assertion(
                "dev.primary",
                "equals",
                ref("result", "/primary"),
                ref("inputs", "/facts/primary"),
                description="Copy the visible primary fact.",
                repair_group="dev.pair",
            ),
            assertion(
                "dev.dependent",
                "equals",
                ref("result", "/dependent"),
                ref("inputs", "/facts/dependent"),
                description="Copy the visible dependent fact.",
                repair_group="dev.pair",
            ),
        ]
        contract = contract_for(
            rules,
            {"facts": {"primary": sentinel, "dependent": "already-correct"}},
        )

        result = SemanticAssertionsGate().evaluate(
            contract,
            Proposal(
                result={"primary": "wrong", "dependent": "already-correct"}
            ),
        )

        scope = result.details["repair_scope"]
        self.assertEqual(scope["failed_rule_ids"], ["dev.primary"])
        self.assertEqual(scope["rule_ids"], ["dev.dependent", "dev.primary"])
        self.assertEqual(
            scope["editable_paths"],
            ["/result/dependent", "/result/primary"],
        )
        self.assertEqual(
            scope["atomic_groups"],
            [
                {
                    "group_id": "dev.pair",
                    "editable_paths": [
                        "/result/dependent",
                        "/result/primary",
                    ],
                }
            ],
        )
        self.assertEqual(
            scope["source_pointers"],
            [
                {"source": "contract.inputs", "pointer": "/facts/dependent"},
                {"source": "contract.inputs", "pointer": "/facts/primary"},
            ],
        )
        serialized = json.dumps(result.to_feedback().to_dict())
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("expected_value", serialized)
        self.assertNotIn("actual_value", serialized)

    def test_policy_and_scope_parsers_fail_closed(self):
        parsed = RuleAwareRepairPolicy.from_raw(
            repair_policy(immutable_paths=["/result/approval"])
        )
        self.assertEqual(parsed.immutable_paths, ("/result/approval",))

        invalid = [
            {**repair_policy(), "unknown": True},
            {**repair_policy(), "preserve_unlisted": False},
            {**repair_policy(), "mode": "unbounded"},
            repair_policy(immutable_paths=["result/approval"]),
            repair_policy(
                immutable_paths=["/result/approval", "/result/approval"]
            ),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                RuleAwareRepairPolicy.from_raw(raw)

        null_policy_contract = contract_for([], {})
        null_policy_inputs = null_policy_contract.to_dict()["inputs"]
        null_policy_inputs["repair_policy"] = None
        null_policy_raw = null_policy_contract.to_dict()
        null_policy_raw["inputs"] = null_policy_inputs
        with self.assertRaises(ValueError):
            RuleAwareRepairPolicy.from_contract(
                TaskContract.from_dict(null_policy_raw)
            )

        scope = {
            "schema_version": "1.0",
            "failed_rule_ids": ["dev.rule"],
            "rule_ids": ["dev.rule"],
            "editable_paths": ["/result/value"],
            "atomic_groups": [],
            "source_pointers": [
                {"source": "contract.inputs", "pointer": "/facts/value"}
            ],
        }
        self.assertEqual(
            RepairScope.from_raw(scope).editable_paths,
            ("/result/value",),
        )
        with self.assertRaises(ValueError):
            RepairScope.from_raw({**scope, "editable_paths": [""]})
        with self.assertRaises(ValueError):
            RepairScope.from_raw(
                {
                    **scope,
                    "source_pointers": [
                        {
                            "source": "contract.inputs",
                            "pointer": "/expected_result",
                        }
                    ],
                }
            )

    def test_scope_check_rejects_sibling_and_immutable_changes(self):
        scope = RepairScope.from_raw(
            {
                "schema_version": "1.0",
                "failed_rule_ids": ["dev.status"],
                "rule_ids": ["dev.status"],
                "editable_paths": ["/result/approval", "/result/status"],
                "atomic_groups": [],
                "source_pointers": [
                    {"source": "contract.inputs", "pointer": "/rules/status"}
                ],
            }
        )
        policy = RuleAwareRepairPolicy.from_raw(
            repair_policy(immutable_paths=["/result/approval"])
        )
        baseline = Proposal(result={"status": "review", "approval": True})

        allowed = assess_candidate_changes(
            baseline,
            Proposal(result={"status": "ready", "approval": True}),
            scope,
            policy,
        )
        sibling = assess_candidate_changes(
            baseline,
            Proposal(result={"status": "ready", "approval": False}),
            scope,
            policy,
        )

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.changed_paths, ("/result/status",))
        self.assertFalse(sibling.allowed)
        self.assertEqual(sibling.reason_code, "repair.change_out_of_scope")
        self.assertEqual(sibling.outside_paths, ("/result/approval",))

    def test_scope_check_is_type_strict_and_supports_array_elements(self):
        scope = RepairScope.from_raw(
            {
                "schema_version": "1.0",
                "failed_rule_ids": ["dev.item"],
                "rule_ids": ["dev.item"],
                "editable_paths": ["/result/items/0/status"],
                "atomic_groups": [],
                "source_pointers": [],
            }
        )
        policy = RuleAwareRepairPolicy.from_raw(repair_policy())
        baseline = Proposal(
            result={
                "items": [{"status": "pending"}],
                "stable": {"amount": 1},
            }
        )

        allowed = assess_candidate_changes(
            baseline,
            Proposal(
                result={
                    "items": [{"status": "ready"}],
                    "stable": {"amount": 1},
                }
            ),
            scope,
            policy,
        )
        type_change = assess_candidate_changes(
            baseline,
            Proposal(
                result={
                    "items": [{"status": "ready"}],
                    "stable": {"amount": 1.0},
                }
            ),
            scope,
            policy,
        )
        structural_change = assess_candidate_changes(
            baseline,
            Proposal(
                result={
                    "items": [
                        {"status": "ready"},
                        {"status": "extra"},
                    ],
                    "stable": {"amount": 1},
                }
            ),
            scope,
            policy,
        )
        signed_zero = assess_candidate_changes(
            Proposal(result={"score": 1, "immutable": -0.0}),
            Proposal(result={"score": 11, "immutable": 0.0}),
            RepairScope.from_raw(
                {
                    "schema_version": "1.0",
                    "failed_rule_ids": ["dev.score"],
                    "rule_ids": ["dev.score"],
                    "editable_paths": ["/result/score"],
                    "atomic_groups": [],
                    "source_pointers": [],
                }
            ),
            policy,
        )

        self.assertTrue(allowed.allowed)
        self.assertEqual(
            allowed.changed_paths,
            ("/result/items/0/status",),
        )
        self.assertFalse(type_change.allowed)
        self.assertIn("/result/stable/amount", type_change.outside_paths)
        self.assertFalse(structural_change.allowed)
        self.assertIn("/result/items", structural_change.outside_paths)
        self.assertFalse(signed_zero.allowed)
        self.assertIn("/result/immutable", signed_zero.outside_paths)

    def test_semantic_scope_marks_only_direct_result_target_writable(self):
        rules = [
            assertion(
                "dev.total",
                "equals",
                ref("result", "/total"),
                expression(
                    "add",
                    ref("result", "/part_a"),
                    ref("result", "/part_b"),
                ),
            )
        ]
        contract = contract_for(rules, {})

        result = SemanticAssertionsGate().evaluate(
            contract,
            Proposal(result={"total": 0, "part_a": 2, "part_b": 3}),
        )

        self.assertEqual(
            result.details["repair_scope"]["editable_paths"],
            ["/result/total"],
        )

    def test_ambiguous_result_to_result_rule_is_not_repair_signaled(self):
        rules = [
            assertion(
                "dev.ambiguous",
                "equals",
                ref("result", "/left"),
                ref("result", "/right"),
            )
        ]
        contract = contract_for(rules, {})

        result = SemanticAssertionsGate().evaluate(
            contract,
            Proposal(result={"left": 1, "right": 2}),
        )

        self.assertNotIn("repair_scope", result.details)

    def test_missing_repair_target_is_not_signaled_as_writable(self):
        rules = [
            assertion(
                "dev.missing",
                "equals",
                ref("result", "/nested/value"),
                ref("inputs", "/expected"),
            )
        ]
        contract = contract_for(rules, {"expected": 1})

        result = SemanticAssertionsGate().evaluate(
            contract,
            Proposal(result={}),
        )

        self.assertNotIn("repair_scope", result.details)

    def test_unmarked_gate_cannot_inject_a_repair_scope(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(rules, {"minimum": 10})

        class UnmarkedGate:
            name = "unmarked"

            def evaluate(self, contract, proposal):
                del contract, proposal
                return GateResult(
                    self.name,
                    False,
                    "Untrusted scope-shaped details.",
                    failure_code="unmarked.failed",
                    details={
                        "repair_scope": {
                            "schema_version": "1.0",
                            "failed_rule_ids": ["secret-derived-id"],
                            "rule_ids": ["secret-derived-id"],
                            "editable_paths": ["/result/score"],
                            "atomic_groups": [],
                            "source_pointers": [],
                        }
                    },
                )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                RepairRuntime(Proposal(result={"score": 1}), []),
                (SemanticAssertionsGate(), UnmarkedGate()),
                ledger,
            ).run(contract)

        self.assertEqual(
            result.reason_code,
            RunReasonCode.UNSIGNALED_FAILURE,
        )

    def test_controller_accepts_only_scoped_strict_improvement(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(rules, {"minimum": 10})
        runtime = RepairRuntime(
            Proposal(result={"score": 3, "stable": "keep"}),
            [Proposal(result={"score": 11, "stable": "keep"})],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            events = tuple(ledger.records(result.run_id))

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.proposal.result, {"score": 11, "stable": "keep"})
        self.assertEqual(runtime.propose_calls, 1)
        self.assertEqual(len(runtime.repair_calls), 1)
        baseline, feedback, scope = runtime.repair_calls[0]
        self.assertEqual(baseline.result, {"score": 3, "stable": "keep"})
        self.assertEqual(scope["editable_paths"], ["/result/score"])
        self.assertEqual(feedback[0].gate, "repair_policy")
        self.assertTrue(
            any(item.event_type == "repair.candidate.adopted" for item in events)
        )

    def test_controller_rolls_back_out_of_scope_regression(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(rules, {"minimum": 10})
        baseline = Proposal(
            result={"score": 3, "requires_human_approval": True}
        )
        runtime = RepairRuntime(
            baseline,
            [
                Proposal(
                    result={"score": 11, "requires_human_approval": False}
                )
            ],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            rejected = [
                item.payload
                for item in ledger.records(result.run_id)
                if item.event_type == "repair.candidate.rejected"
            ]

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.ATTEMPT_BUDGET_EXHAUSTED,
        )
        self.assertEqual(result.proposal, baseline)
        self.assertEqual(rejected[0]["reason_code"], "repair.change_out_of_scope")
        self.assertEqual(rejected[0]["outside_paths"], [
            "/result/requires_human_approval"
        ])

    def test_runtime_cannot_mutate_controller_owned_baseline(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(
            rules,
            {"minimum": 10},
            immutable_paths=["/result/approval"],
        )
        baseline = Proposal(result={"score": 1, "approval": True})

        class MutatingRuntime(RepairRuntime):
            def repair(self, contract, baseline, feedback, repair_scope):
                del contract
                self.repair_calls.append(
                    (baseline, tuple(feedback), repair_scope)
                )
                baseline.result["score"] = 11
                baseline.result["approval"] = False
                return baseline

        runtime = MutatingRuntime(baseline, [])

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(result.proposal.result, {"score": 1, "approval": True})
        self.assertEqual(baseline.result, {"score": 1, "approval": True})

    def test_runtime_cannot_mutate_controller_owned_contract_inputs(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/rules/minimum"),
            )
        ]
        contract = contract_for(
            rules,
            {"rules": {"minimum": 10}},
            max_attempts=1,
        )

        class ContractMutatingRuntime:
            def propose(self, contract, feedback):
                del feedback
                contract.inputs["rules"]["minimum"] = -100
                return Proposal(result={"score": 0})

        with EvidenceLedger() as ledger:
            result = DohaaController(
                ContractMutatingRuntime(),
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(contract.inputs["rules"]["minimum"], 10)
        self.assertFalse(result.gate_results[0].passed)

    def test_deterministic_repair_cannot_change_immutable_target(self):
        rules = [
            assertion(
                "dev.approval",
                "equals",
                ref("result", "/approval"),
                ref("inputs", "/required_approval"),
            )
        ]
        contract = contract_for(
            rules,
            {"required_approval": True},
            max_attempts=1,
            immutable_paths=["/result/approval"],
        )
        runtime = RepairRuntime(Proposal(result={"approval": False}), [])

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            events = tuple(ledger.records(result.run_id))

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(result.proposal.result, {"approval": False})
        self.assertFalse(
            any(
                event.event_type == "semantic.repair.applied"
                for event in events
            )
        )

    def test_deterministic_repair_is_filtered_to_selected_unit(self):
        rules = [
            assertion(
                "dev.first",
                "equals",
                ref("result", "/first"),
                ref("inputs", "/expected_first"),
            ),
            assertion(
                "dev.second",
                "equals",
                ref("result", "/second"),
                ref("inputs", "/expected_second"),
            ),
        ]
        contract = contract_for(
            rules,
            {"expected_first": 1, "expected_second": 2},
        )
        runtime = RepairRuntime(
            Proposal(result={"first": 0, "second": 0}),
            [Proposal(result={"first": 1, "second": 2})],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            events = tuple(ledger.records(result.run_id))

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.proposal.result, {"first": 1, "second": 2})
        self.assertEqual(
            runtime.repair_calls[0][0].result,
            {"first": 1, "second": 0},
        )
        self.assertTrue(
            any(
                event.event_type == "semantic.repair.partially_applied"
                for event in events
            )
        )

    def test_controller_rolls_back_non_improvement_and_retains_partial_best(self):
        rules = [
            assertion(
                "dev.first",
                "greater_than",
                ref("result", "/first"),
                ref("inputs", "/minimum"),
            ),
            assertion(
                "dev.second",
                "greater_than",
                ref("result", "/second"),
                ref("inputs", "/minimum"),
            ),
        ]
        contract = contract_for(rules, {"minimum": 10}, max_attempts=3)
        baseline = Proposal(result={"first": 1, "second": 2})
        partial = Proposal(result={"first": 11, "second": 2})
        no_better = Proposal(result={"first": 11, "second": 3})
        runtime = RepairRuntime(baseline, [partial, no_better])

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            decisions = [
                (item.event_type, item.payload.get("reason_code"))
                for item in ledger.records(result.run_id)
                if item.event_type.startswith("repair.candidate.")
            ]

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(result.proposal, partial)
        self.assertEqual(
            decisions,
            [
                ("repair.candidate.adopted", "repair.strict_improvement"),
                ("repair.candidate.rejected", "repair.target_not_resolved"),
            ],
        )

    def test_shared_target_requires_every_failed_rule_to_resolve(self):
        rules = [
            assertion(
                "dev.above",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/lower"),
            ),
            assertion(
                "dev.below",
                "less_than",
                ref("result", "/score"),
                ref("inputs", "/upper"),
            ),
        ]
        contract = contract_for(rules, {"lower": 10, "upper": 0})
        baseline = Proposal(result={"score": 5})
        runtime = RepairRuntime(baseline, [Proposal(result={"score": 11})])

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            rejected = next(
                event
                for event in ledger.records(result.run_id)
                if event.event_type == "repair.candidate.rejected"
            )

        self.assertEqual(result.proposal, baseline)
        self.assertEqual(
            rejected.payload["reason_code"],
            "repair.target_not_resolved",
        )

    def test_atomic_group_regression_is_rejected_as_one_candidate(self):
        rules = [
            assertion(
                "dev.a",
                "greater_than",
                ref("result", "/a"),
                ref("inputs", "/minimum"),
                repair_group="dev.atomic",
            ),
            assertion(
                "dev.b",
                "greater_than",
                ref("result", "/b"),
                ref("inputs", "/minimum"),
                repair_group="dev.atomic",
            ),
        ]
        contract = contract_for(rules, {"minimum": 10})
        baseline = Proposal(result={"a": 1, "b": 11})
        runtime = RepairRuntime(
            baseline,
            [Proposal(result={"a": 11, "b": 1})],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)
            rejected = next(
                item
                for item in ledger.records(result.run_id)
                if item.event_type == "repair.candidate.rejected"
            )

        self.assertEqual(result.proposal, baseline)
        self.assertEqual(
            rejected.payload["reason_code"],
            "repair.failure_regression",
        )

    def test_visible_budget_rules_use_deterministic_arithmetic(self):
        obligated = expression(
            "add",
            ref("inputs", "/budget/spent_minor"),
            ref("inputs", "/budget/committed_minor"),
            ref("inputs", "/budget/reserve_minor"),
        )
        rules = [
            assertion(
                "dev.budget.obligated",
                "equals",
                ref("result", "/total_obligated_minor"),
                obligated,
                repair_group="dev.budget.totals",
            ),
            assertion(
                "dev.budget.available",
                "equals",
                ref("result", "/total_available_minor"),
                expression(
                    "subtract",
                    ref("inputs", "/budget/approved_minor"),
                    obligated,
                ),
                repair_group="dev.budget.totals",
            ),
        ]
        contract = contract_for(
            rules,
            {
                "budget": {
                    "approved_minor": 803_113,
                    "spent_minor": 301_009,
                    "committed_minor": 151_013,
                    "reserve_minor": 50_017,
                }
            },
        )
        runtime = RepairRuntime(
            Proposal(
                result={
                    "total_obligated_minor": 452_022,
                    "total_available_minor": 351_091,
                }
            ),
            [],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(
            result.proposal.result,
            {
                "total_obligated_minor": 502_039,
                "total_available_minor": 301_074,
            },
        )

    def test_visible_payment_rules_use_deterministic_business_calendar(self):
        review_date = expression(
            "add_business_days",
            ref("inputs", "/schedule/start_date"),
            ref("inputs", "/schedule/terms_business_days"),
            ref("inputs", "/schedule/holidays"),
        )
        rules = [
            assertion(
                "dev.payment.review",
                "equals",
                ref("result", "/scheduled_review_date"),
                review_date,
                repair_group="dev.payment.dates",
            ),
            assertion(
                "dev.payment.approval",
                "equals",
                ref("result", "/approval_deadline"),
                expression(
                    "add_business_days",
                    review_date,
                    ref("inputs", "/schedule/approval_offset_business_days"),
                    ref("inputs", "/schedule/holidays"),
                ),
                repair_group="dev.payment.dates",
            ),
        ]
        contract = contract_for(
            rules,
            {
                "schedule": {
                    "start_date": "2029-03-02",
                    "terms_business_days": 3,
                    "approval_offset_business_days": -2,
                    "holidays": ["2029-03-05"],
                }
            },
        )
        runtime = RepairRuntime(
            Proposal(
                result={
                    "scheduled_review_date": "2029-03-07",
                    "approval_deadline": "2029-03-08",
                }
            ),
            [],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(),),
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(
            result.proposal.result,
            {
                "scheduled_review_date": "2029-03-08",
                "approval_deadline": "2029-03-06",
            },
        )

    def test_hidden_exact_oracle_never_enters_repair_feedback(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(rules, {"minimum": 10})
        runtime = RepairRuntime(
            Proposal(result={"score": 3}),
            [Proposal(result={"score": 11})],
        )
        hidden_expected = {"score": "PRIVATE-ORACLE-SENTINEL"}

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(), ResultEqualsGate(hidden_expected)),
                ledger,
            ).run(contract)

        serialized = json.dumps(
            [item.to_dict() for item in runtime.repair_calls[0][1]]
        )
        self.assertNotIn("result_equals", serialized)
        self.assertNotIn("PRIVATE-ORACLE-SENTINEL", serialized)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.UNSIGNALED_FAILURE,
        )
        self.assertEqual(result.proposal.result, {"score": 11})

    def test_oracle_only_improvement_never_becomes_next_baseline(self):
        rules = [
            assertion(
                "dev.minimum",
                "greater_than",
                ref("result", "/score"),
                ref("inputs", "/minimum"),
            )
        ]
        contract = contract_for(rules, {"minimum": 10}, max_attempts=3)
        runtime = RepairRuntime(
            Proposal(result={"score": 1}),
            [Proposal(result={"score": 5}), Proposal(result={"score": 11})],
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                (SemanticAssertionsGate(), ResultEqualsGate({"score": 5})),
                ledger,
            ).run(contract)
            decisions = [
                event.payload.get("reason_code")
                for event in ledger.records(result.run_id)
                if event.event_type.startswith("repair.candidate.")
            ]

        self.assertEqual(
            [call[0].result for call in runtime.repair_calls],
            [{"score": 1}, {"score": 1}],
        )
        self.assertEqual(
            decisions,
            ["repair.target_not_resolved", "repair.strict_improvement"],
        )
        self.assertEqual(result.proposal.result, {"score": 11})
        self.assertEqual(result.reason_code, RunReasonCode.UNSIGNALED_FAILURE)

    def test_failure_comparison_uses_rule_identity_not_error_wording(self):
        before = SemanticAssertionsGate().evaluate(
            contract_for(
                [
                    assertion(
                        "dev.rule",
                        "greater_than",
                        ref("result", "/score"),
                        ref("inputs", "/minimum"),
                    )
                ],
                {"minimum": 10},
            ),
            Proposal(result={"score": 1}),
        )
        after = SemanticAssertionsGate().evaluate(
            contract_for(
                [
                    assertion(
                        "dev.rule",
                        "greater_than",
                        ref("result", "/score"),
                        ref("inputs", "/minimum"),
                    )
                ],
                {"minimum": 10},
            ),
            Proposal(result={}),
        )
        comparison = compare_failure_sets((before,), (after,))
        self.assertFalse(comparison.accepted)
        self.assertEqual(
            comparison.reason_code,
            "repair.no_strict_improvement",
        )


if __name__ == "__main__":
    unittest.main()
