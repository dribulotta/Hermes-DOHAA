"""Paired evaluation runner with deterministic scoring and bounded calls."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    Gate,
    RequiredEvidenceGate,
    ResultEqualsGate,
)
from hermes_dohaa.controller.engine import DohaaController, RunReasonCode
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evaluation.models import EvaluationCase, EvaluationSuite
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import AgentRuntime, Proposal, VerifierFeedback


class EvaluationCondition(StrEnum):
    DIRECT = "direct"
    SELF_REFLECTION = "self_reflection"
    DOHAA = "dohaa"


class RuntimeFactory(Protocol):
    def __call__(
        self,
        contract: TaskContract,
        session_id: str,
    ) -> AgentRuntime:
        """Return a fresh runtime isolated to one case and condition."""


@dataclass(slots=True)
class _ObservedRuntime:
    delegate: AgentRuntime
    calls: int = 0
    elapsed_seconds: float = 0.0
    proposals: tuple[Proposal, ...] = ()

    def propose(self, contract, feedback):
        started = time.perf_counter()
        self.calls += 1
        try:
            proposal = self.delegate.propose(contract, feedback)
        finally:
            self.elapsed_seconds += time.perf_counter() - started
        self.proposals = (*self.proposals, proposal)
        return proposal


def run_comparative_evaluation(
    suite: EvaluationSuite,
    runtime_factory: RuntimeFactory,
    *,
    seed: int = 0,
    runtime_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("evaluation seed must be an integer")
    policy = _json_clone(dict(runtime_policy or {}))

    evaluation_id = str(uuid4())
    started_at = _utc_now()
    generator = random.Random(seed)
    case_results = []

    for case in suite.cases:
        conditions = list(EvaluationCondition)
        generator.shuffle(conditions)
        outcomes: dict[str, Any] = {}
        for condition in conditions:
            session_id = _session_id(evaluation_id, case.case_id, condition)
            outcomes[condition.value] = _run_condition(
                case,
                condition,
                runtime_factory,
                session_id,
            )
        case_results.append(
            {
                "case_id": case.case_id,
                "domain": case.domain,
                "execution_order": [item.value for item in conditions],
                "conditions": outcomes,
            }
        )

    completed_at = _utc_now()
    return {
        "schema_version": "1.0",
        "evaluation_id": evaluation_id,
        "suite_id": suite.suite_id,
        "suite_sha256": suite.sha256(),
        "seed": seed,
        "runtime_policy": policy,
        "started_at": started_at,
        "completed_at": completed_at,
        "conditions": [item.value for item in EvaluationCondition],
        "cases": case_results,
        "summary": _summarize(case_results),
        "paired_comparisons": {
            "dohaa_vs_direct": _paired_comparison(
                case_results,
                EvaluationCondition.DOHAA,
                EvaluationCondition.DIRECT,
            ),
            "dohaa_vs_self_reflection": _paired_comparison(
                case_results,
                EvaluationCondition.DOHAA,
                EvaluationCondition.SELF_REFLECTION,
            ),
            "self_reflection_vs_direct": _paired_comparison(
                case_results,
                EvaluationCondition.SELF_REFLECTION,
                EvaluationCondition.DIRECT,
            ),
        },
    }


def write_evaluation_result(path: str | Path, result: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _run_condition(
    case: EvaluationCase,
    condition: EvaluationCondition,
    runtime_factory: RuntimeFactory,
    session_id: str,
) -> dict[str, Any]:
    try:
        runtime = _ObservedRuntime(
            runtime_factory(case.contract, session_id)
        )
    except Exception as exc:
        return _runtime_failure(case, condition, None, exc)

    if condition is EvaluationCondition.DIRECT:
        return _run_direct(case, runtime)
    if condition is EvaluationCondition.SELF_REFLECTION:
        return _run_reflection(case, runtime)
    return _run_dohaa(case, runtime)


def _run_direct(
    case: EvaluationCase,
    runtime: _ObservedRuntime,
) -> dict[str, Any]:
    try:
        proposal = runtime.propose(case.contract, ())
    except Exception as exc:
        return _runtime_failure(
            case,
            EvaluationCondition.DIRECT,
            runtime,
            exc,
        )
    return _completed_outcome(
        case,
        EvaluationCondition.DIRECT,
        runtime,
        proposal,
    )


def _run_reflection(
    case: EvaluationCase,
    runtime: _ObservedRuntime,
) -> dict[str, Any]:
    try:
        first = runtime.propose(case.contract, ())
        feedback = VerifierFeedback(
            gate="self_reflection",
            code="reflection.review",
            reason=(
                "Independently review the previous proposal for factual, logical, "
                "policy, and evidence errors. Return a corrected proposal when "
                "needed. Previous proposal JSON: "
                f"{_canonical_json(first.to_dict())}"
            ),
        )
        final = runtime.propose(case.contract, (feedback,))
    except Exception as exc:
        return _runtime_failure(
            case,
            EvaluationCondition.SELF_REFLECTION,
            runtime,
            exc,
        )
    return _completed_outcome(
        case,
        EvaluationCondition.SELF_REFLECTION,
        runtime,
        final,
    )


def _run_dohaa(
    case: EvaluationCase,
    runtime: _ObservedRuntime,
) -> dict[str, Any]:
    gates = _evaluation_gates(case)
    with EvidenceLedger() as ledger:
        result = DohaaController(runtime, gates, ledger).run(case.contract)
        chain_valid = ledger.verify_chain()
        records = tuple(ledger.records(result.run_id))
    if result.reason_code is RunReasonCode.RUNTIME_FAILED:
        failure = next(
            (
                record
                for record in reversed(records)
                if record.event_type == "runtime.failed"
            ),
            None,
        )
        failure_payload = failure.payload if failure is not None else {}
        outcome = _runtime_failure_details(
            case,
            EvaluationCondition.DOHAA,
            runtime,
            error_type=str(failure_payload.get("error_type", "RuntimeError")),
            error=str(failure_payload.get("error", result.reason)),
        )
        outcome["controller"] = {
            "run_id": result.run_id,
            "status": result.status.value,
            "attempts": result.attempts,
            "reason_code": result.reason_code.value,
            "reason": result.reason,
            "ledger_chain_valid": chain_valid,
        }
        return outcome
    final = result.proposal
    outcome = _completed_outcome(
        case,
        EvaluationCondition.DOHAA,
        runtime,
        final,
    )
    outcome["controller"] = {
        "run_id": result.run_id,
        "status": result.status.value,
        "attempts": result.attempts,
        "reason_code": result.reason_code.value,
        "reason": result.reason,
        "ledger_chain_valid": chain_valid,
    }
    return outcome


def _completed_outcome(
    case: EvaluationCase,
    condition: EvaluationCondition,
    runtime: _ObservedRuntime,
    final: Proposal | None,
) -> dict[str, Any]:
    first = runtime.proposals[0] if runtime.proposals else None
    initial_score = _score(case, first)
    final_score = _score(case, final)
    return {
        "condition": condition.value,
        "status": "completed",
        "runtime_calls": runtime.calls,
        "elapsed_seconds": round(runtime.elapsed_seconds, 6),
        "usage": _usage_records(runtime.delegate),
        "initial_proposal": first.to_dict() if first is not None else None,
        "final_proposal": final.to_dict() if final is not None else None,
        "initial_score": initial_score,
        "final_score": final_score,
        "improved": (
            not initial_score["all_gates_passed"]
            and final_score["all_gates_passed"]
        ),
        "regressed": (
            initial_score["all_gates_passed"]
            and not final_score["all_gates_passed"]
        ),
    }


def _runtime_failure(
    case: EvaluationCase,
    condition: EvaluationCondition,
    runtime: _ObservedRuntime | None,
    error: Exception,
) -> dict[str, Any]:
    return _runtime_failure_details(
        case,
        condition,
        runtime,
        error_type=type(error).__name__,
        error=str(error),
    )


def _runtime_failure_details(
    case: EvaluationCase,
    condition: EvaluationCondition,
    runtime: _ObservedRuntime | None,
    *,
    error_type: str,
    error: str,
) -> dict[str, Any]:
    proposals = runtime.proposals if runtime is not None else ()
    first = proposals[0] if proposals else None
    return {
        "condition": condition.value,
        "status": "runtime_failed",
        "runtime_calls": runtime.calls if runtime is not None else 0,
        "elapsed_seconds": round(
            runtime.elapsed_seconds if runtime is not None else 0.0,
            6,
        ),
        "usage": (
            _usage_records(runtime.delegate)
            if runtime is not None
            else []
        ),
        "initial_proposal": first.to_dict() if first is not None else None,
        "final_proposal": None,
        "initial_score": _score(case, first) if first is not None else None,
        "final_score": None,
        "improved": False,
        "regressed": False,
        "error_type": error_type,
        "error": error,
    }


def _score(
    case: EvaluationCase,
    proposal: Proposal | None,
) -> dict[str, Any]:
    if proposal is None:
        return {
            "all_gates_passed": False,
            "gate_results": [],
        }
    results = tuple(
        gate.evaluate(case.contract, proposal)
        for gate in _evaluation_gates(case)
    )
    return {
        "all_gates_passed": all(result.passed for result in results),
        "gate_results": [result.to_dict() for result in results],
    }


def _evaluation_gates(case: EvaluationCase) -> tuple[Gate, ...]:
    return (
        ResultEqualsGate(case.expected_result),
        ActionPolicyGate(),
        ClaimEvidenceGate(),
        RequiredEvidenceGate(),
    )


def _summarize(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for condition in EvaluationCondition:
        outcomes = [
            case["conditions"][condition.value]
            for case in case_results
        ]
        completed = [item for item in outcomes if item["status"] == "completed"]
        usage = [
            record
            for item in outcomes
            for record in item["usage"]
        ]
        reported_tokens = [
            record["total_tokens"]
            for record in usage
            if _is_number(record.get("total_tokens"))
        ]
        initial_passes = sum(
            bool(item["initial_score"]["all_gates_passed"])
            for item in completed
        )
        final_passes = sum(
            bool(item["final_score"]["all_gates_passed"])
            for item in completed
        )
        summary[condition.value] = {
            "cases": len(outcomes),
            "completed": len(completed),
            "runtime_failures": len(outcomes) - len(completed),
            "initial_passes": initial_passes,
            "final_passes": final_passes,
            "initial_pass_rate": _rate(initial_passes, len(outcomes)),
            "final_pass_rate": _rate(final_passes, len(outcomes)),
            "initial_gate_passes": _gate_pass_counts(
                completed,
                "initial_score",
            ),
            "final_gate_passes": _gate_pass_counts(
                completed,
                "final_score",
            ),
            "improved": sum(bool(item["improved"]) for item in completed),
            "regressed": sum(bool(item["regressed"]) for item in completed),
            "average_runtime_calls": _average(
                [item["runtime_calls"] for item in outcomes]
            ),
            "average_elapsed_seconds": _average(
                [item["elapsed_seconds"] for item in outcomes]
            ),
            "usage_reported_calls": len(reported_tokens),
            "reported_total_tokens": sum(reported_tokens),
            "average_reported_total_tokens_per_case": (
                round(sum(reported_tokens) / len(outcomes), 6)
                if reported_tokens
                else None
            ),
        }
    return summary


def _paired_comparison(
    case_results: list[dict[str, Any]],
    left: EvaluationCondition,
    right: EvaluationCondition,
) -> dict[str, int]:
    wins = 0
    losses = 0
    ties = 0
    for case in case_results:
        left_passed = _outcome_passed(case["conditions"][left.value])
        right_passed = _outcome_passed(case["conditions"][right.value])
        if left_passed and not right_passed:
            wins += 1
        elif right_passed and not left_passed:
            losses += 1
        else:
            ties += 1
    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
    }


def _outcome_passed(outcome: dict[str, Any]) -> bool:
    score = outcome.get("final_score")
    return bool(score and score.get("all_gates_passed"))


def _average(values: list[int | float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def _gate_pass_counts(
    outcomes: list[dict[str, Any]],
    score_key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for result in outcome[score_key]["gate_results"]:
            gate = result["gate"]
            counts.setdefault(gate, 0)
            if result["passed"]:
                counts[gate] += 1
    return dict(sorted(counts.items()))


def _usage_records(runtime: AgentRuntime) -> list[dict[str, Any]]:
    records = getattr(runtime, "usage_records", ())
    if not isinstance(records, (list, tuple)):
        return []
    return [dict(item) for item in records if isinstance(item, dict)]


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _session_id(
    evaluation_id: str,
    case_id: str,
    condition: EvaluationCondition,
) -> str:
    raw = f"{evaluation_id}:{case_id}:{condition.value}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"evaluation:{digest}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
