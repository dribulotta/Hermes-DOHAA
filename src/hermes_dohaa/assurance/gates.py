"""Deterministic gates. No gate may delegate its final verdict to the model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    passed: bool
    reason: str
    evidence_ids: tuple[str, ...] = ()


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
            return GateResult(self.name, False, f"Forbidden actions requested: {sorted(forbidden)}")
        if undeclared:
            return GateResult(self.name, False, f"Actions are not allowlisted: {sorted(undeclared)}")
        return GateResult(self.name, True, "All requested actions are allowlisted")


@dataclass(frozen=True, slots=True)
class ClaimEvidenceGate:
    name: str = "claim_evidence"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        del contract
        available = {item.evidence_id for item in proposal.evidence}
        duplicate_count = len(proposal.evidence) - len(available)
        if duplicate_count:
            return GateResult(self.name, False, "Evidence IDs must be unique")
        missing = {
            evidence_id
            for claim in proposal.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in available
        }
        unsupported = [claim.statement for claim in proposal.claims if not claim.evidence_ids]
        if missing:
            return GateResult(self.name, False, f"Claims reference missing evidence: {sorted(missing)}")
        if unsupported:
            return GateResult(self.name, False, "Every claim must reference at least one evidence item")
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
            return GateResult(self.name, False, f"Required evidence is missing: {sorted(missing)}")
        return GateResult(
            self.name,
            True,
            "All contract-required evidence IDs are present",
            tuple(sorted(required)),
        )
