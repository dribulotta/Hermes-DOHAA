import unittest

from hermes_dohaa.assurance.gates import ActionPolicyGate, ClaimEvidenceGate, RequiredEvidenceGate
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import (
    DohaaController,
    RunReasonCode,
    RunStatus,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Claim, EvidenceItem, Proposal
from test_contracts import valid_contract


class SequenceRuntime:
    def __init__(self, proposals):
        self.proposals = iter(proposals)
        self.feedback_seen = []

    def propose(self, contract, feedback):
        del contract
        self.feedback_seen.append(tuple(feedback))
        return next(self.proposals)


def passing_proposal():
    evidence = EvidenceItem.create("source-1", "artifact", "fixture", {"ok": True})
    return Proposal(
        result={"summary": "verified"},
        claims=(Claim("The source says ok", ("source-1",)),),
        evidence=(evidence,),
        requested_actions=("artifact.read",),
    )


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.gates = (ActionPolicyGate(), ClaimEvidenceGate(), RequiredEvidenceGate())

    def test_retries_failed_proposal_then_succeeds(self):
        bad = Proposal(result={"summary": "unsupported"})
        runtime = SequenceRuntime([bad, passing_proposal()])
        with EvidenceLedger() as ledger:
            result = DohaaController(runtime, self.gates, ledger).run(
                TaskContract.from_dict(valid_contract())
            )
            self.assertTrue(ledger.verify_chain())
            retry_events = [
                record.payload
                for record in ledger.records()
                if record.event_type == "state.changed"
                and record.payload.get("status") == "retrying"
            ]
        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.reason_code, RunReasonCode.SUCCEEDED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(runtime.feedback_seen[1]), 1)
        feedback = runtime.feedback_seen[1][0]
        self.assertEqual(feedback.gate, "required_evidence")
        self.assertEqual(feedback.code, "evidence.required_missing")
        self.assertEqual(retry_events[0]["feedback"], [feedback.to_dict()])

    def test_repeated_failed_proposal_escalates_for_no_progress(self):
        bad = Proposal(result={"summary": "unsupported"})
        runtime = SequenceRuntime([bad, bad])
        with EvidenceLedger() as ledger:
            result = DohaaController(runtime, self.gates, ledger).run(
                TaskContract.from_dict(valid_contract())
            )
        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertIn("No progress", result.reason)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.NO_PROGRESS,
        )

    def test_runtime_failure_has_stable_reason_code(self):
        class FailingRuntime:
            def propose(self, contract, feedback):
                del contract, feedback
                raise RuntimeError("provider unavailable")

        with EvidenceLedger() as ledger:
            result = DohaaController(
                FailingRuntime(),
                self.gates,
                ledger,
            ).run(TaskContract.from_dict(valid_contract()))
            finished = [
                record
                for record in ledger.records(result.run_id)
                if record.event_type == "run.finished"
            ]

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.RUNTIME_FAILED,
        )
        self.assertEqual(len(finished), 1)
        self.assertEqual(
            finished[0].payload["reason_code"],
            RunReasonCode.RUNTIME_FAILED.value,
        )

    def test_attempt_budget_has_stable_reason_code(self):
        runtime = SequenceRuntime(
            [
                Proposal(result={"attempt": 1}),
                Proposal(result={"attempt": 2}),
            ]
        )
        contract = TaskContract.from_dict(
            valid_contract(max_attempts=2)
        )

        with EvidenceLedger() as ledger:
            result = DohaaController(
                runtime,
                self.gates,
                ledger,
            ).run(contract)

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.ATTEMPT_BUDGET_EXHAUSTED,
        )

    def test_human_gate_is_enforced_after_assurance(self):
        contract = TaskContract.from_dict(
            valid_contract(risk_level="critical", requires_human_approval=True)
        )
        with EvidenceLedger() as ledger:
            result = DohaaController(
                SequenceRuntime([passing_proposal()]), self.gates, ledger
            ).run(contract)
        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertIn("human approval", result.reason)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.HUMAN_APPROVAL_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
