"""Bounded deterministic controller for untrusted agent proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable
from uuid import uuid4

from hermes_dohaa.assurance.gates import Gate, GateResult
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import AgentRuntime, Proposal


class RunStatus(StrEnum):
    RECEIVED = "received"
    PROPOSING = "proposing"
    VERIFYING = "verifying"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    ESCALATED = "escalated"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    attempts: int
    proposal: Proposal | None
    gate_results: tuple[GateResult, ...]
    reason: str


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
        feedback: list[str] = []
        fingerprints: set[str] = set()
        last_proposal: Proposal | None = None
        last_gate_results: tuple[GateResult, ...] = ()

        for attempt in range(1, contract.max_attempts + 1):
            self._record(run_id, "state.changed", {"status": RunStatus.PROPOSING, "attempt": attempt})
            try:
                proposal = self.runtime.propose(contract, feedback)
            except Exception as exc:  # Runtime errors are evidence, never controller crashes.
                self._record(
                    run_id,
                    "runtime.failed",
                    {"attempt": attempt, "error_type": type(exc).__name__, "error": str(exc)},
                )
                return self._finish(
                    run_id,
                    RunStatus.ESCALATED,
                    attempt,
                    last_proposal,
                    last_gate_results,
                    "Cognitive runtime failed",
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
                    "results": [
                        {
                            "gate": result.gate,
                            "passed": result.passed,
                            "reason": result.reason,
                            "evidence_ids": list(result.evidence_ids),
                        }
                        for result in last_gate_results
                    ],
                },
            )

            if all(result.passed for result in last_gate_results):
                if contract.requires_human_approval and not human_approved:
                    return self._finish(
                        run_id,
                        RunStatus.ESCALATED,
                        attempt,
                        proposal,
                        last_gate_results,
                        "All gates passed; explicit human approval is still required",
                    )
                return self._finish(
                    run_id,
                    RunStatus.SUCCEEDED,
                    attempt,
                    proposal,
                    last_gate_results,
                    "All deterministic gates passed",
                )

            feedback = [result.reason for result in last_gate_results if not result.passed]
            self._record(
                run_id,
                "state.changed",
                {"status": RunStatus.RETRYING, "attempt": attempt, "feedback": feedback},
            )

        return self._finish(
            run_id,
            RunStatus.ESCALATED,
            contract.max_attempts,
            last_proposal,
            last_gate_results,
            "Attempt budget exhausted",
        )

    def _finish(
        self,
        run_id: str,
        status: RunStatus,
        attempts: int,
        proposal: Proposal | None,
        gate_results: tuple[GateResult, ...],
        reason: str,
    ) -> RunResult:
        self._record(
            run_id,
            "run.finished",
            {"status": status, "attempts": attempts, "reason": reason},
        )
        return RunResult(run_id, status, attempts, proposal, gate_results, reason)

    def _record(self, run_id: str, event_type: str, payload: object) -> None:
        self.ledger.append(run_id, event_type, payload)
