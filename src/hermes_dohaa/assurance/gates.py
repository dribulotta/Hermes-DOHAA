"""Deterministic gates. No gate may delegate its final verdict to the model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback


class GateFailureCode(StrEnum):
    RESULT_MISMATCH = "result.mismatch"
    ACTION_FORBIDDEN = "action.forbidden"
    ACTION_NOT_ALLOWLISTED = "action.not_allowlisted"
    EVIDENCE_DUPLICATE_ID = "evidence.duplicate_id"
    EVIDENCE_REFERENCE_MISSING = "evidence.reference_missing"
    EVIDENCE_CLAIM_UNSUPPORTED = "evidence.claim_unsupported"
    EVIDENCE_REQUIRED_MISSING = "evidence.required_missing"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    passed: bool
    reason: str
    evidence_ids: tuple[str, ...] = ()
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.passed and self.failure_code is not None:
            raise ValueError(
                "passing gate results cannot have a failure code"
            )
        if not self.passed:
            if (
                not isinstance(self.failure_code, str)
                or not self.failure_code.strip()
            ):
                raise ValueError(
                    "failing gate results require a failure code"
                )

    def to_feedback(self) -> VerifierFeedback:
        if self.passed or self.failure_code is None:
            raise ValueError(
                "only failing gate results can become feedback"
            )
        return VerifierFeedback(
            gate=self.gate,
            code=self.failure_code,
            reason=self.reason,
            evidence_ids=self.evidence_ids,
        )


class Gate(Protocol):
    name: str

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        """Produce a deterministic verdict for one proposal."""


@dataclass(frozen=True, slots=True)
class ActionPolicyGate:
    name: str = "action_policy"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        requested = set(proposal.requested_actions)
        forbidden = requested & contract.forbidden_actions
        undeclared = requested - contract.allowed_actions
        if forbidden:
            return GateResult(
                self.name,
                False,
                f"Forbidden actions requested: {sorted(forbidden)}",
                failure_code=GateFailureCode.ACTION_FORBIDDEN,
            )
        if undeclared:
            return GateResult(
                self.name,
                False,
                f"Actions are not allowlisted: {sorted(undeclared)}",
                failure_code=GateFailureCode.ACTION_NOT_ALLOWLISTED,
            )
        return GateResult(self.name, True, "All requested actions are allowlisted")


@dataclass(frozen=True, slots=True)
class ClaimEvidenceGate:
    name: str = "claim_evidence"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        del contract
        identifiers = [
            item.evidence_id
            for item in proposal.evidence
        ]
        available = set(identifiers)
        duplicate_ids = tuple(
            sorted(
                evidence_id
                for evidence_id, count in Counter(identifiers).items()
                if count > 1
            )
        )
        if duplicate_ids:
            return GateResult(
                self.name,
                False,
                "Evidence IDs must be unique",
                duplicate_ids,
                failure_code=GateFailureCode.EVIDENCE_DUPLICATE_ID,
            )
        missing = {
            evidence_id
            for claim in proposal.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in available
        }
        unsupported = [claim.statement for claim in proposal.claims if not claim.evidence_ids]
        if missing:
            return GateResult(
                self.name,
                False,
                f"Claims reference missing evidence: {sorted(missing)}",
                tuple(sorted(missing)),
                failure_code=GateFailureCode.EVIDENCE_REFERENCE_MISSING,
            )
        if unsupported:
            return GateResult(
                self.name,
                False,
                "Every claim must reference at least one evidence item",
                failure_code=GateFailureCode.EVIDENCE_CLAIM_UNSUPPORTED,
            )
        return GateResult(self.name, True, "Every claim references available evidence", tuple(sorted(available)))


@dataclass(frozen=True, slots=True)
class RequiredEvidenceGate:
    name: str = "required_evidence"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        required = {
            evidence_id
            for criterion in contract.acceptance_criteria
            for evidence_id in criterion.required_evidence
        }
        available = {item.evidence_id for item in proposal.evidence}
        missing = required - available
        if missing:
            return GateResult(
                self.name,
                False,
                f"Required evidence is missing: {sorted(missing)}",
                tuple(sorted(missing)),
                failure_code=GateFailureCode.EVIDENCE_REQUIRED_MISSING,
            )
        return GateResult(
            self.name,
            True,
            "All contract-required evidence IDs are present",
            tuple(sorted(required)),
        )


@dataclass(frozen=True, slots=True)
class ResultEqualsGate:
    """Require the proposal result to equal a controller-owned expected value."""

    expected: Any
    name: str = "result_equals"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        del contract
        if proposal.result != self.expected:
            return GateResult(
                self.name,
                False,
                "Proposal result does not equal the expected value",
                failure_code=GateFailureCode.RESULT_MISMATCH,
            )
        return GateResult(self.name, True, "Proposal result equals the expected value")
