"""Bounded deterministic controller for untrusted agent proposals."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping
from uuid import uuid4

from hermes_dohaa.assurance.gates import Gate, GateResult
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.identity import (
    ControlPlaneIdentity,
    ControlPlaneIdentityError,
    capture_control_plane_identity,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import (
    AgentRuntime,
    Proposal,
    VerifierFeedback,
)
from hermes_dohaa.runtime.hermes_api import HermesApiError


class RunStatus(StrEnum):
    RECEIVED = "received"
    PROPOSING = "proposing"
    VERIFYING = "verifying"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    ESCALATED = "escalated"
    FAILED = "failed"


class RunReasonCode(StrEnum):
    SUCCEEDED = "run.succeeded"
    RUNTIME_FAILED = "runtime.failed"
    NO_PROGRESS = "repair.no_progress"
    ATTEMPT_BUDGET_EXHAUSTED = "budget.exhausted"
    HUMAN_APPROVAL_REQUIRED = "approval.required"
    CONTROL_PLANE_IDENTITY_FAILED = "control_plane.identity_failed"


class RunResumeErrorCode(StrEnum):
    NOT_FOUND = "resume.not_found"
    NOT_ELIGIBLE = "resume.not_eligible"
    CONTRACT_MISMATCH = "resume.contract_mismatch"
    APPROVAL_MISSING = "resume.approval_missing"
    CHECKPOINT_INVALID = "resume.checkpoint_invalid"
    CONTROL_PLANE_MISMATCH = "resume.control_plane_mismatch"


class RunResumeError(RuntimeError):
    def __init__(self, code: RunResumeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    schema_version: str
    run_id: str
    contract_sha256: str
    attempt: int
    proposal: Proposal
    proposal_fingerprint: str
    gate_results: tuple[GateResult, ...]
    control_plane: ControlPlaneIdentity
    reason_code: RunReasonCode

    def __post_init__(self) -> None:
        if self.schema_version != "1.1":
            raise ValueError(
                f"unsupported checkpoint schema: {self.schema_version!r}"
            )
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("checkpoint run_id must be a non-empty string")
        if (
            not isinstance(self.contract_sha256, str)
            or len(self.contract_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.contract_sha256)
        ):
            raise ValueError("checkpoint contract_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise ValueError("checkpoint attempt must be an integer")
        if self.attempt < 1:
            raise ValueError("checkpoint attempt must be positive")
        if (
            not isinstance(self.proposal_fingerprint, str)
            or self.proposal.fingerprint() != self.proposal_fingerprint
        ):
            raise ValueError("checkpoint proposal fingerprint does not match its payload")
        if not self.gate_results or not all(
            result.passed for result in self.gate_results
        ):
            raise ValueError("approval checkpoints require passing gate results")
        gate_names = [result.gate for result in self.gate_results]
        if len(gate_names) != len(set(gate_names)):
            raise ValueError("checkpoint gate names must be unique")
        if not isinstance(self.control_plane, ControlPlaneIdentity):
            raise ValueError(
                "checkpoint control_plane must be a ControlPlaneIdentity"
            )
        if self.reason_code is not RunReasonCode.HUMAN_APPROVAL_REQUIRED:
            raise ValueError("checkpoint reason must be approval.required")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RunCheckpoint":
        allowed = {
            "schema_version",
            "run_id",
            "contract_sha256",
            "attempt",
            "proposal",
            "proposal_fingerprint",
            "gate_results",
            "control_plane",
            "reason_code",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                f"unknown checkpoint fields: {sorted(unknown)}"
            )
        proposal_raw = raw.get("proposal")
        gate_results_raw = raw.get("gate_results")
        control_plane_raw = raw.get("control_plane")
        if not isinstance(proposal_raw, dict):
            raise ValueError("checkpoint proposal must be an object")
        if not isinstance(gate_results_raw, list) or not all(
            isinstance(item, dict) for item in gate_results_raw
        ):
            raise ValueError("checkpoint gate_results must be a list of objects")
        if not isinstance(control_plane_raw, dict):
            raise ValueError("checkpoint control_plane must be an object")
        try:
            reason_code = RunReasonCode(raw.get("reason_code"))
        except ValueError as exc:
            raise ValueError("checkpoint reason_code is invalid") from exc
        return cls(
            schema_version=raw.get("schema_version"),
            run_id=raw.get("run_id"),
            contract_sha256=raw.get("contract_sha256"),
            attempt=raw.get("attempt"),
            proposal=Proposal.from_dict(proposal_raw),
            proposal_fingerprint=raw.get("proposal_fingerprint"),
            gate_results=tuple(
                GateResult.from_dict(item) for item in gate_results_raw
            ),
            control_plane=ControlPlaneIdentity.from_dict(control_plane_raw),
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "contract_sha256": self.contract_sha256,
            "attempt": self.attempt,
            "proposal": self.proposal.to_dict(),
            "proposal_fingerprint": self.proposal_fingerprint,
            "gate_results": [result.to_dict() for result in self.gate_results],
            "control_plane": self.control_plane.to_dict(),
            "reason_code": self.reason_code.value,
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    attempts: int
    proposal: Proposal | None
    gate_results: tuple[GateResult, ...]
    reason_code: RunReasonCode
    reason: str
    runtime_error_code: str | None = None
    runtime_error_details: dict[str, Any] | None = None


class DohaaController:
    def __init__(
        self,
        runtime: AgentRuntime,
        gates: Iterable[Gate],
        ledger: EvidenceLedger,
    ) -> None:
        self.runtime = runtime
        self.gates = tuple(gates)
        if not self.gates:
            raise ValueError("At least one assurance gate is required")
        self.ledger = ledger

    def run(self, contract: TaskContract, *, human_approved: bool = False) -> RunResult:
        run_id = str(uuid4())
        self._record(run_id, "run.received", {"contract": contract.to_dict()})
        feedback: list[VerifierFeedback] = []
        fingerprints: set[str] = set()
        last_proposal: Proposal | None = None
        last_gate_results: tuple[GateResult, ...] = ()

        for attempt in range(1, contract.max_attempts + 1):
            self._record(run_id, "state.changed", {"status": RunStatus.PROPOSING, "attempt": attempt})
            try:
                proposal = self.runtime.propose(contract, feedback)
            except Exception as exc:  # Runtime errors are evidence, never controller crashes.
                runtime_error_code = (
                    exc.code if isinstance(exc, HermesApiError) else None
                )
                runtime_error_details = (
                    exc.to_dict()["details"]
                    if isinstance(exc, HermesApiError)
                    else {}
                )
                safe_error = (
                    exc.message if isinstance(exc, HermesApiError) else str(exc)
                )
                self._record(
                    run_id,
                    "runtime.failed",
                    {
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": safe_error,
                        "runtime_error_code": runtime_error_code,
                        "runtime_error_details": runtime_error_details,
                    },
                )
                return self._finish(
                    run_id,
                    RunStatus.ESCALATED,
                    attempt,
                    last_proposal,
                    last_gate_results,
                    RunReasonCode.RUNTIME_FAILED,
                    "Cognitive runtime failed",
                    runtime_error_code=runtime_error_code,
                    runtime_error_details=runtime_error_details,
                )

            last_proposal = proposal
            fingerprint = proposal.fingerprint()
            self._record(
                run_id,
                "proposal.received",
                {
                    "attempt": attempt,
                    "fingerprint": fingerprint,
                    "claims": len(proposal.claims),
                    "evidence": len(proposal.evidence),
                    "requested_actions": list(proposal.requested_actions),
                },
            )

            if fingerprint in fingerprints:
                return self._finish(
                    run_id,
                    RunStatus.ESCALATED,
                    attempt,
                    proposal,
                    last_gate_results,
                    RunReasonCode.NO_PROGRESS,
                    "No progress: the runtime repeated an earlier proposal",
                )
            fingerprints.add(fingerprint)

            self._record(run_id, "state.changed", {"status": RunStatus.VERIFYING, "attempt": attempt})
            last_gate_results = tuple(gate.evaluate(contract, proposal) for gate in self.gates)
            self._record(
                run_id,
                "gates.evaluated",
                {
                    "attempt": attempt,
                    "results": [result.to_dict() for result in last_gate_results],
                },
            )

            if all(result.passed for result in last_gate_results):
                if contract.requires_human_approval and not human_approved:
                    try:
                        control_plane = capture_control_plane_identity(
                            self.gates
                        )
                    except ControlPlaneIdentityError as exc:
                        self._record(
                            run_id,
                            "control_plane.failed",
                            {
                                "attempt": attempt,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            },
                        )
                        return self._finish(
                            run_id,
                            RunStatus.ESCALATED,
                            attempt,
                            proposal,
                            last_gate_results,
                            RunReasonCode.CONTROL_PLANE_IDENTITY_FAILED,
                            "Control-plane identity could not be captured",
                        )
                    checkpoint = RunCheckpoint(
                        schema_version="1.1",
                        run_id=run_id,
                        contract_sha256=_contract_sha256(contract),
                        attempt=attempt,
                        proposal=proposal,
                        proposal_fingerprint=proposal.fingerprint(),
                        gate_results=last_gate_results,
                        control_plane=control_plane,
                        reason_code=RunReasonCode.HUMAN_APPROVAL_REQUIRED,
                    )
                    with self.ledger.transaction():
                        self._record(
                            run_id,
                            "run.checkpointed",
                            checkpoint.to_dict(),
                        )
                        return self._finish(
                            run_id,
                            RunStatus.ESCALATED,
                            attempt,
                            proposal,
                            last_gate_results,
                            RunReasonCode.HUMAN_APPROVAL_REQUIRED,
                            "All gates passed; explicit human approval is still required",
                        )
                return self._finish(
                    run_id,
                    RunStatus.SUCCEEDED,
                    attempt,
                    proposal,
                    last_gate_results,
                    RunReasonCode.SUCCEEDED,
                    "All deterministic gates passed",
                )

            feedback = [
                result.to_feedback()
                for result in last_gate_results
                if not result.passed
            ]
            self._record(
                run_id,
                "state.changed",
                {
                    "status": RunStatus.RETRYING,
                    "attempt": attempt,
                    "feedback": [item.to_dict() for item in feedback],
                },
            )

        return self._finish(
            run_id,
            RunStatus.ESCALATED,
            contract.max_attempts,
            last_proposal,
            last_gate_results,
            RunReasonCode.ATTEMPT_BUDGET_EXHAUSTED,
            "Attempt budget exhausted",
        )

    def resume(
        self,
        contract: TaskContract,
        run_id: str,
        *,
        human_approved: bool = False,
    ) -> RunResult:
        with self.ledger.transaction():
            return self._resume_locked(
                contract,
                run_id,
                human_approved=human_approved,
            )

    def _resume_locked(
        self,
        contract: TaskContract,
        run_id: str,
        *,
        human_approved: bool,
    ) -> RunResult:
        if not isinstance(run_id, str) or not run_id.strip():
            raise RunResumeError(
                RunResumeErrorCode.NOT_FOUND,
                "resume run_id must be a non-empty string",
            )

        self.ledger.verify_chain()
        records = tuple(self.ledger.records(run_id))
        if not records:
            raise RunResumeError(
                RunResumeErrorCode.NOT_FOUND,
                f"run ID not found: {run_id}",
            )

        finished = tuple(
            record for record in records if record.event_type == "run.finished"
        )
        if not finished:
            raise RunResumeError(
                RunResumeErrorCode.NOT_ELIGIBLE,
                "run has no terminal approval checkpoint",
            )
        terminal = finished[-1]
        if terminal.payload.get("reason_code") != RunReasonCode.HUMAN_APPROVAL_REQUIRED.value:
            raise RunResumeError(
                RunResumeErrorCode.NOT_ELIGIBLE,
                "only runs stopped at approval.required can be resumed",
            )
        if terminal.payload.get("status") != RunStatus.ESCALATED.value:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "approval terminal event has an invalid status",
            )
        if terminal.sequence != records[-1].sequence:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "approval terminal event is not the latest event for the run",
            )
        if len(records) < 2 or records[-2].event_type != "run.checkpointed":
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "approval checkpoint is missing or out of order",
            )
        checkpoint_record = records[-2]
        try:
            checkpoint = RunCheckpoint.from_dict(checkpoint_record.payload)
        except (TypeError, ValueError) as exc:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                f"approval checkpoint is invalid: {exc}",
            ) from exc

        if checkpoint.run_id != run_id:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "approval checkpoint belongs to a different run",
            )
        if terminal.payload.get("attempts") != checkpoint.attempt:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "approval checkpoint attempt does not match the terminal event",
            )
        checkpoint_gate_names = tuple(
            result.gate for result in checkpoint.gate_results
        )
        current_gate_names = tuple(gate.name for gate in self.gates)
        if checkpoint_gate_names != current_gate_names:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                "configured gates do not match the approval checkpoint",
            )
        try:
            current_control_plane = capture_control_plane_identity(self.gates)
        except ControlPlaneIdentityError as exc:
            raise RunResumeError(
                RunResumeErrorCode.CHECKPOINT_INVALID,
                f"current control-plane identity cannot be captured: {exc}",
            ) from exc
        if current_control_plane.sha256 != checkpoint.control_plane.sha256:
            raise RunResumeError(
                RunResumeErrorCode.CONTROL_PLANE_MISMATCH,
                "current control plane does not match the approval checkpoint",
            )
        if checkpoint.contract_sha256 != _contract_sha256(contract):
            raise RunResumeError(
                RunResumeErrorCode.CONTRACT_MISMATCH,
                "task contract does not match the approval checkpoint",
            )
        if not human_approved:
            raise RunResumeError(
                RunResumeErrorCode.APPROVAL_MISSING,
                "explicit human approval is required to resume this run",
            )

        self._record(
            run_id,
            "run.resumed",
            {
                "checkpoint_sequence": checkpoint_record.sequence,
                "contract_sha256": checkpoint.contract_sha256,
                "control_plane_sha256": checkpoint.control_plane.sha256,
                "from_reason_code": checkpoint.reason_code.value,
                "human_approved": True,
            },
        )
        return self._finish(
            run_id,
            RunStatus.SUCCEEDED,
            checkpoint.attempt,
            checkpoint.proposal,
            checkpoint.gate_results,
            RunReasonCode.SUCCEEDED,
            "All deterministic gates passed and explicit human approval was supplied",
        )

    def _finish(
        self,
        run_id: str,
        status: RunStatus,
        attempts: int,
        proposal: Proposal | None,
        gate_results: tuple[GateResult, ...],
        reason_code: RunReasonCode,
        reason: str,
        *,
        runtime_error_code: str | None = None,
        runtime_error_details: dict[str, Any] | None = None,
    ) -> RunResult:
        self._record(
            run_id,
            "run.finished",
            {
                "status": status,
                "attempts": attempts,
                "reason_code": reason_code,
                "reason": reason,
                "runtime_error_code": runtime_error_code,
                "runtime_error_details": runtime_error_details or {},
            },
        )
        return RunResult(
            run_id,
            status,
            attempts,
            proposal,
            gate_results,
            reason_code,
            reason,
            runtime_error_code,
            (
                runtime_error_details.copy()
                if runtime_error_details is not None
                else None
            ),
        )

    def _record(self, run_id: str, event_type: str, payload: object) -> None:
        self.ledger.append(run_id, event_type, payload)


def _contract_sha256(contract: TaskContract) -> str:
    return hashlib.sha256(contract.canonical_json().encode("utf-8")).hexdigest()
