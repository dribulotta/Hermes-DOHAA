import json
import unittest

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    RequiredEvidenceGate,
    ResultEqualsGate,
    SemanticAssertionsGate,
)
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import (
    DohaaController,
    RunReasonCode,
    RunStatus,
)
from hermes_dohaa.controller.semantic_repair import (
    propose_deterministic_semantic_repair,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_contracts import valid_contract


def ref(source, pointer):
    return {"op": "ref", "source": source, "pointer": pointer}


def expression(op, *args):
    return {"op": op, "args": list(args)}


def assertion(assertion_id, operator, left, right):
    return {
        "assertion_id": assertion_id,
        "operator": operator,
        "left": left,
        "right": right,
    }


def contract_for(inputs, *, max_attempts=2, forbidden_actions=None):
    return TaskContract.from_dict(
        valid_contract(
            inputs=inputs,
            max_attempts=max_attempts,
            allowed_actions=[],
            forbidden_actions=forbidden_actions or [],
            acceptance_criteria=[
                {
                    "criterion_id": "visible-semantics",
                    "description": "Satisfy the visible deterministic relations.",
                    "required_evidence": [],
                }
            ],
        )
    )


class RecordingRuntime:
    def __init__(self, proposals):
        self.proposals = iter(proposals)
        self.calls = 0

    def propose(self, contract, feedback):
        del contract, feedback
        self.calls += 1
        return next(self.proposals)


class DeterministicSemanticRepairTests(unittest.TestCase):
    def test_repairs_multiple_visible_temporal_equalities_without_mutation(self):
        inputs = {
            "window": {
                "start": "2026-10-03T08:15:00Z",
                "end": "2026-10-03T11:00:00Z",
            },
            "calendar": {
                "start": "2026-10-05",
                "days": 3,
                "holidays": ["2026-10-07"],
            },
            "semantic_assertions": [
                assertion(
                    "elapsed",
                    "equals",
                    ref("result", "/elapsed_minutes"),
                    expression(
                        "duration_minutes",
                        ref("inputs", "/window/start"),
                        ref("inputs", "/window/end"),
                    ),
                ),
                assertion(
                    "deadline",
                    "equals",
                    expression(
                        "add_business_days",
                        ref("inputs", "/calendar/start"),
                        ref("inputs", "/calendar/days"),
                        ref("inputs", "/calendar/holidays"),
                    ),
                    ref("result", "/deadline"),
                ),
            ],
        }
        original = Proposal(
            result={"elapsed_minutes": 200, "deadline": "2026-10-08"}
        )

        repair = propose_deterministic_semantic_repair(
            contract_for(inputs),
            original,
        )

        self.assertIsNotNone(repair)
        self.assertEqual(
            repair.proposal.result,
            {"elapsed_minutes": 165, "deadline": "2026-10-09"},
        )
        self.assertEqual(
            repair.assertion_ids,
            ("deadline", "elapsed"),
        )
        self.assertEqual(
            repair.result_pointers,
            ("/deadline", "/elapsed_minutes"),
        )
        self.assertEqual(
            original.result,
            {"elapsed_minutes": 200, "deadline": "2026-10-08"},
        )

    def test_conflicts_result_dependencies_and_non_equals_are_not_repaired(self):
        common = {
            "first": 10,
            "second": 20,
            "semantic_assertions": [
                assertion(
                    "first-value",
                    "equals",
                    ref("result", "/value"),
                    ref("inputs", "/first"),
                ),
                assertion(
                    "conflicting-value",
                    "equals",
                    ref("result", "/value"),
                    ref("inputs", "/second"),
                ),
            ],
        }
        self.assertIsNone(
            propose_deterministic_semantic_repair(
                contract_for(common),
                Proposal(result={"value": 0}),
            )
        )

        for item in (
            assertion(
                "result-dependent",
                "equals",
                ref("result", "/value"),
                expression(
                    "add",
                    ref("result", "/other"),
                    ref("inputs", "/first"),
                ),
            ),
            assertion(
                "ordered-only",
                "less_than",
                ref("result", "/value"),
                ref("inputs", "/first"),
            ),
        ):
            inputs = {"first": 10, "semantic_assertions": [item]}
            self.assertIsNone(
                propose_deterministic_semantic_repair(
                    contract_for(inputs),
                    Proposal(result={"value": 30, "other": 20}),
                )
            )

    def test_missing_or_overlapping_result_pointers_fail_closed(self):
        missing = {
            "fact": 3,
            "semantic_assertions": [
                assertion(
                    "missing",
                    "equals",
                    ref("result", "/missing"),
                    ref("inputs", "/fact"),
                )
            ],
        }
        self.assertIsNone(
            propose_deterministic_semantic_repair(
                contract_for(missing),
                Proposal(result={"present": 0}),
            )
        )

        overlapping = {
            "whole": {"child": 1},
            "child": 2,
            "semantic_assertions": [
                assertion(
                    "whole",
                    "equals",
                    ref("result", "/nested"),
                    ref("inputs", "/whole"),
                ),
                assertion(
                    "child",
                    "equals",
                    ref("result", "/nested/child"),
                    ref("inputs", "/child"),
                ),
            ],
        }
        self.assertIsNone(
            propose_deterministic_semantic_repair(
                contract_for(overlapping),
                Proposal(result={"nested": {"child": 0}}),
            )
        )

    def test_controller_accepts_revalidated_repair_with_one_runtime_call(self):
        secret_visible_fact = "synthetic-private-visible-value"
        inputs = {
            "fact": secret_visible_fact,
            "semantic_assertions": [
                assertion(
                    "copy-visible-fact",
                    "equals",
                    ref("result", "/answer"),
                    ref("inputs", "/fact"),
                )
            ],
        }
        original = Proposal(result={"answer": "wrong"})
        runtime = RecordingRuntime([original])
        gates = (
            SemanticAssertionsGate(),
            ResultEqualsGate({"answer": secret_visible_fact}),
            ActionPolicyGate(),
            ClaimEvidenceGate(),
            RequiredEvidenceGate(),
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(runtime, gates, ledger).run(
                contract_for(inputs)
            )
            repair_events = [
                record
                for record in ledger.records(result.run_id)
                if record.event_type == "semantic.repair.applied"
            ]
            gate_events = [
                record
                for record in ledger.records(result.run_id)
                if record.event_type == "gates.evaluated"
            ]

        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.reason_code, RunReasonCode.SUCCEEDED)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(runtime.calls, 1)
        self.assertEqual(result.proposal.result, {"answer": secret_visible_fact})
        self.assertEqual(original.result, {"answer": "wrong"})
        self.assertEqual(len(gate_events), 2)
        self.assertEqual(
            gate_events[-1].payload["source"],
            "deterministic_semantic_repair",
        )
        self.assertEqual(
            repair_events[0].payload,
            {
                "assertion_ids": ["copy-visible-fact"],
                "result_pointers": ["/answer"],
            },
        )
        self.assertNotIn(
            secret_visible_fact,
            json.dumps(repair_events[0].payload),
        )

    def test_repair_is_rejected_when_other_gate_still_fails(self):
        inputs = {
            "fact": "correct",
            "semantic_assertions": [
                assertion(
                    "copy-fact",
                    "equals",
                    ref("result", "/answer"),
                    ref("inputs", "/fact"),
                )
            ],
        }
        original = Proposal(
            result={"answer": "wrong"},
            requested_actions=("shell.execute",),
        )
        runtime = RecordingRuntime([original])
        gates = (SemanticAssertionsGate(), ActionPolicyGate())

        with EvidenceLedger() as ledger:
            result = DohaaController(runtime, gates, ledger).run(
                contract_for(
                    inputs,
                    max_attempts=1,
                    forbidden_actions=["shell.execute"],
                )
            )
            events = tuple(ledger.records(result.run_id))

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.ATTEMPT_BUDGET_EXHAUSTED,
        )
        self.assertEqual(result.proposal, original)
        self.assertEqual(runtime.calls, 1)
        self.assertEqual(
            sum(record.event_type == "gates.evaluated" for record in events),
            2,
        )
        rejected = next(
            record
            for record in events
            if record.event_type == "semantic.repair.rejected"
        )
        self.assertEqual(rejected.payload["result_pointers"], ["/answer"])


if __name__ == "__main__":
    unittest.main()
