import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    GateResult,
    RequiredEvidenceGate,
)
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import (
    DohaaController,
    RunCheckpoint,
    RunReasonCode,
    RunResumeError,
    RunResumeErrorCode,
    RunStatus,
)
from hermes_dohaa.controller.identity import capture_control_plane_identity
from hermes_dohaa.evidence.ledger import EvidenceLedger, LedgerIntegrityError
from hermes_dohaa.runtime.base import Claim, EvidenceItem, Proposal
from hermes_dohaa.runtime.hermes_api import HermesApiError
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


class AlternateActionPolicyGate:
    name = "action_policy"

    def evaluate(self, contract, proposal):
        del contract, proposal
        return None


class NonJsonPassGate:
    name = "fixture"

    def __init__(self):
        self.opaque = object()

    def evaluate(self, contract, proposal):
        del contract, proposal
        return GateResult(self.name, True, "Fixture passed")


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

    def test_structured_runtime_failure_is_safe_in_ledger_and_terminal_event(self):
        secret = "Authorization: Bearer private-value"

        class FailingRuntime:
            def propose(self, contract, feedback):
                del contract, feedback
                raise HermesApiError(
                    "proposal.content_non_json",
                    "Hermes returned non-JSON proposal content",
                    {"stage": "proposal_content", "byte_length": len(secret)},
                )

        with EvidenceLedger() as ledger:
            result = DohaaController(FailingRuntime(), self.gates, ledger).run(
                TaskContract.from_dict(valid_contract())
            )
            payloads = [record.payload for record in ledger.records(result.run_id)]

        serialized = json.dumps(payloads)
        self.assertNotIn(secret, serialized)
        self.assertEqual(result.reason_code, RunReasonCode.RUNTIME_FAILED)
        self.assertEqual(result.runtime_error_code, "proposal.content_non_json")
        self.assertTrue(all("contract" not in payload for payload in payloads[-2:]))
        self.assertEqual(
            payloads[-1]["runtime_error_details"]["byte_length"], len(secret)
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

    def test_approval_checkpoint_resumes_without_runtime_call(self):
        contract = TaskContract.from_dict(
            valid_contract(
                risk_level="critical",
                requires_human_approval=True,
            )
        )
        runtime = SequenceRuntime([passing_proposal()])

        with EvidenceLedger() as ledger:
            controller = DohaaController(runtime, self.gates, ledger)
            pending = controller.run(contract)
            resumed = controller.resume(
                contract,
                pending.run_id,
                human_approved=True,
            )
            events = [
                record.event_type
                for record in ledger.records(pending.run_id)
            ]
            self.assertTrue(ledger.verify_chain())

            with self.assertRaises(RunResumeError) as raised:
                controller.resume(
                    contract,
                    pending.run_id,
                    human_approved=True,
                )

        self.assertEqual(pending.reason_code, RunReasonCode.HUMAN_APPROVAL_REQUIRED)
        self.assertEqual(resumed.status, RunStatus.SUCCEEDED)
        self.assertEqual(resumed.reason_code, RunReasonCode.SUCCEEDED)
        self.assertEqual(resumed.run_id, pending.run_id)
        self.assertEqual(resumed.attempts, pending.attempts)
        self.assertEqual(
            resumed.proposal.fingerprint(),
            pending.proposal.fingerprint(),
        )
        self.assertEqual(len(runtime.feedback_seen), 1)
        self.assertEqual(events.count("run.checkpointed"), 1)
        self.assertEqual(events.count("run.resumed"), 1)
        self.assertEqual(events.count("run.finished"), 2)
        self.assertEqual(
            raised.exception.code,
            RunResumeErrorCode.NOT_ELIGIBLE,
        )

    def test_resume_rejects_missing_approval_and_contract_mismatch(self):
        contract = TaskContract.from_dict(
            valid_contract(
                risk_level="critical",
                requires_human_approval=True,
            )
        )
        changed_contract = TaskContract.from_dict(
            valid_contract(
                objective="A different objective",
                risk_level="critical",
                requires_human_approval=True,
            )
        )

        with EvidenceLedger() as ledger:
            controller = DohaaController(
                SequenceRuntime([passing_proposal()]),
                self.gates,
                ledger,
            )
            pending = controller.run(contract)

            with self.assertRaises(RunResumeError) as missing_approval:
                controller.resume(contract, pending.run_id)
            with self.assertRaises(RunResumeError) as mismatch:
                controller.resume(
                    changed_contract,
                    pending.run_id,
                    human_approved=True,
                )
            with self.assertRaises(RunResumeError) as gate_mismatch:
                DohaaController(
                    SequenceRuntime([]),
                    (ActionPolicyGate(),),
                    ledger,
                ).resume(
                    contract,
                    pending.run_id,
                    human_approved=True,
                )
            with self.assertRaises(RunResumeError) as control_plane_mismatch:
                DohaaController(
                    SequenceRuntime([]),
                    (
                        AlternateActionPolicyGate(),
                        ClaimEvidenceGate(),
                        RequiredEvidenceGate(),
                    ),
                    ledger,
                ).resume(
                    contract,
                    pending.run_id,
                    human_approved=True,
                )
            with self.assertRaises(RunResumeError) as missing_run:
                controller.resume(
                    contract,
                    "missing-run",
                    human_approved=True,
                )

        self.assertEqual(
            missing_approval.exception.code,
            RunResumeErrorCode.APPROVAL_MISSING,
        )
        self.assertEqual(
            mismatch.exception.code,
            RunResumeErrorCode.CONTRACT_MISMATCH,
        )
        self.assertEqual(
            gate_mismatch.exception.code,
            RunResumeErrorCode.CHECKPOINT_INVALID,
        )
        self.assertEqual(
            control_plane_mismatch.exception.code,
            RunResumeErrorCode.CONTROL_PLANE_MISMATCH,
        )
        self.assertEqual(
            missing_run.exception.code,
            RunResumeErrorCode.NOT_FOUND,
        )

    def test_checkpoint_rejects_modified_proposal_payload(self):
        proposal = passing_proposal()
        gate_results = tuple(
            gate.evaluate(
                TaskContract.from_dict(valid_contract()),
                proposal,
            )
            for gate in self.gates
        )
        checkpoint = RunCheckpoint(
            schema_version="1.1",
            run_id="run-a",
            contract_sha256="a" * 64,
            attempt=1,
            proposal=proposal,
            proposal_fingerprint=proposal.fingerprint(),
            gate_results=gate_results,
            control_plane=capture_control_plane_identity(self.gates),
            reason_code=RunReasonCode.HUMAN_APPROVAL_REQUIRED,
        ).to_dict()

        legacy_checkpoint = dict(checkpoint)
        legacy_checkpoint["schema_version"] = "1.0"
        with self.assertRaisesRegex(ValueError, "unsupported checkpoint schema"):
            RunCheckpoint.from_dict(legacy_checkpoint)

        checkpoint["proposal"]["result"] = {"summary": "modified"}

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            RunCheckpoint.from_dict(checkpoint)

        checkpoint = RunCheckpoint(
            schema_version="1.1",
            run_id="run-a",
            contract_sha256="a" * 64,
            attempt=1,
            proposal=proposal,
            proposal_fingerprint=proposal.fingerprint(),
            gate_results=gate_results,
            control_plane=capture_control_plane_identity(self.gates),
            reason_code=RunReasonCode.HUMAN_APPROVAL_REQUIRED,
        ).to_dict()
        checkpoint["proposal"]["evidence"][0]["sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "sha256"):
            RunCheckpoint.from_dict(checkpoint)

    def test_checkpoint_identity_failure_escalates_without_checkpoint(self):
        contract = TaskContract.from_dict(
            valid_contract(
                risk_level="critical",
                requires_human_approval=True,
            )
        )
        with EvidenceLedger() as ledger:
            result = DohaaController(
                SequenceRuntime([passing_proposal()]),
                (NonJsonPassGate(),),
                ledger,
            ).run(contract)
            events = tuple(ledger.records(result.run_id))

        self.assertEqual(result.status, RunStatus.ESCALATED)
        self.assertEqual(
            result.reason_code,
            RunReasonCode.CONTROL_PLANE_IDENTITY_FAILED,
        )
        self.assertTrue(
            any(record.event_type == "control_plane.failed" for record in events)
        )
        self.assertFalse(
            any(record.event_type == "run.checkpointed" for record in events)
        )

    def test_resume_rejects_a_tampered_ledger_before_appending(self):
        contract = TaskContract.from_dict(
            valid_contract(
                risk_level="critical",
                requires_human_approval=True,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with EvidenceLedger(path) as ledger:
                pending = DohaaController(
                    SequenceRuntime([passing_proposal()]),
                    self.gates,
                    ledger,
                ).run(contract)

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE ledger_events SET payload_json = ? WHERE sequence = 1",
                ('{"tampered":true}',),
            )
            connection.commit()
            connection.close()

            with EvidenceLedger(path, create=False) as ledger:
                count_before = ledger.record_count()
                with self.assertRaises(LedgerIntegrityError):
                    DohaaController(
                        SequenceRuntime([]),
                        self.gates,
                        ledger,
                    ).resume(
                        contract,
                        pending.run_id,
                        human_approved=True,
                    )
                self.assertEqual(ledger.record_count(), count_before)


if __name__ == "__main__":
    unittest.main()
