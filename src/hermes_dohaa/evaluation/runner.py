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
    PolicyDecisionGate,
    PolicyReasonCodeGate,
    RequiredEvidenceGate,
    ResultEqualsGate,
    ResultSpecGate,
    SemanticAssertionsGate,
)
from hermes_dohaa.controller.engine import DohaaController, RunReasonCode
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.assurance.result_spec import json_equal, json_type
from hermes_dohaa.evaluation.models import EvaluationCase, EvaluationSuite
from hermes_dohaa.evaluation.statistics import analyze_unique_cases
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import (
    AgentRuntime,
    Proposal,
    RuleAwareRepairUnavailableError,
    VerifierFeedback,
)
from hermes_dohaa.runtime.hermes_api import HermesApiError
from hermes_dohaa.runtime.usage import summarize_usage


class EvaluationCondition(StrEnum):
    DIRECT = "direct"
    SELF_REFLECTION = "self_reflection"
    DOHAA = "dohaa"


class RuntimeFactory(Protocol):
    def __call__(
        self,
        contract: TaskContract,
        session_id: str,
        sampling_seed: int,
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
            proposal = self.delegate.propose(
                _snapshot_contract(contract),
                _snapshot_feedback(feedback),
            )
        finally:
            self.elapsed_seconds += time.perf_counter() - started
        proposal = _snapshot_proposal(proposal)
        self.proposals = (*self.proposals, proposal)
        return proposal

    def repair(self, contract, baseline, feedback, repair_scope):
        method = getattr(self.delegate, "repair", None)
        if not callable(method):
            raise RuleAwareRepairUnavailableError(
                "runtime does not implement scoped repair"
            )
        started = time.perf_counter()
        self.calls += 1
        try:
            proposal = method(
                _snapshot_contract(contract),
                _snapshot_proposal(baseline),
                _snapshot_feedback(feedback),
                _json_clone(repair_scope),
            )
        finally:
            self.elapsed_seconds += time.perf_counter() - started
        proposal = _snapshot_proposal(proposal)
        self.proposals = (*self.proposals, proposal)
        return proposal


def run_comparative_evaluation(
    suite: EvaluationSuite,
    runtime_factory: RuntimeFactory,
    *,
    seed: int = 0,
    repetitions: int = 1,
    sampling_seed: int = 0,
    runtime_policy: Mapping[str, Any] | None = None,
    suite_commitment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Detach the authoritative evaluation data before any untrusted factory or
    # runtime sees it.  Every later runtime call receives another disposable
    # clone; scoring continues against this untouched snapshot.
    suite = EvaluationSuite.from_dict(suite.to_dict())
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("evaluation seed must be an integer")
    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or not 1 <= repetitions <= 100
    ):
        raise ValueError("evaluation repetitions must be between 1 and 100")
    if isinstance(sampling_seed, bool) or not isinstance(sampling_seed, int):
        raise ValueError("evaluation sampling_seed must be an integer")
    policy = _json_clone(dict(runtime_policy or {}))
    commitment = (
        _json_clone(dict(suite_commitment))
        if suite_commitment is not None
        else None
    )

    evaluation_id = str(uuid4())
    started_at = _utc_now()
    generator = random.Random(seed)
    case_results = []

    for case in suite.cases:
        for repetition in range(1, repetitions + 1):
            trial_sampling_seed = _trial_sampling_seed(
                sampling_seed,
                case.case_id,
                repetition,
            )
            conditions = list(EvaluationCondition)
            generator.shuffle(conditions)
            outcomes: dict[str, Any] = {}
            for condition in conditions:
                session_id = _session_id(
                    evaluation_id,
                    case.case_id,
                    repetition,
                    condition,
                )
                outcomes[condition.value] = _run_condition(
                    case,
                    condition,
                    runtime_factory,
                    session_id,
                    trial_sampling_seed,
                )
            case_results.append(
                {
                    "case_id": case.case_id,
                    "domain": case.domain,
                    "repetition": repetition,
                    "sampling_seed": trial_sampling_seed,
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
        "repetitions": repetitions,
        "sampling_seed": sampling_seed,
        "runtime_policy": policy,
        "suite_commitment": commitment,
        "suite_commitment_sha256": (
            hashlib.sha256(
                _canonical_json(commitment).encode("utf-8")
            ).hexdigest()
            if commitment is not None
            else None
        ),
        "started_at": started_at,
        "completed_at": completed_at,
        "conditions": [item.value for item in EvaluationCondition],
        "cases": case_results,
        "summary": _summarize(case_results),
        "statistical_analysis": analyze_unique_cases(case_results),
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
    if not target.parent.is_dir():
        raise FileNotFoundError(
            f"evaluation output directory does not exist: {target.parent}"
        )
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
    sampling_seed: int,
) -> dict[str, Any]:
    try:
        runtime = _ObservedRuntime(
            runtime_factory(
                _snapshot_contract(case.contract),
                session_id,
                sampling_seed,
            )
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
    if result.reason_code in {
        RunReasonCode.RUNTIME_FAILED,
        RunReasonCode.REPAIR_RUNTIME_UNAVAILABLE,
    }:
        failure = next(
            (
                record
                for record in reversed(records)
                if record.event_type == "runtime.failed"
            ),
            None,
        )
        failure_payload = failure.payload if failure is not None else {}
        repair_unavailable = (
            result.reason_code is RunReasonCode.REPAIR_RUNTIME_UNAVAILABLE
        )
        outcome = _runtime_failure_details(
            case,
            EvaluationCondition.DOHAA,
            runtime,
            error_type=(
                "RuleAwareRepairUnavailableError"
                if repair_unavailable
                else str(failure_payload.get("error_type", "RuntimeError"))
            ),
            error=str(failure_payload.get("error", result.reason)),
            error_code=(
                result.reason_code.value
                if repair_unavailable
                else failure_payload.get("runtime_error_code")
            ),
            error_details=failure_payload.get("runtime_error_details", {}),
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
    usage = _usage_records(runtime.delegate)
    return {
        "condition": condition.value,
        "status": "completed",
        "runtime_calls": runtime.calls,
        "elapsed_seconds": round(runtime.elapsed_seconds, 6),
        "usage": usage,
        "usage_summary": summarize_usage(usage, runtime.calls),
        "initial_proposal": (
            _json_clone(first.to_dict()) if first is not None else None
        ),
        "final_proposal": (
            _json_clone(final.to_dict()) if final is not None else None
        ),
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
        "repair_transition": _repair_transition(initial_score, final_score),
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
        error=error.message if isinstance(error, HermesApiError) else str(error),
        error_code=error.code if isinstance(error, HermesApiError) else None,
        error_details=(
            error.to_dict()["details"]
            if isinstance(error, HermesApiError)
            else {}
        ),
    )


def _runtime_failure_details(
    case: EvaluationCase,
    condition: EvaluationCondition,
    runtime: _ObservedRuntime | None,
    *,
    error_type: str,
    error: str,
    error_code: Any = None,
    error_details: Any = None,
) -> dict[str, Any]:
    proposals = runtime.proposals if runtime is not None else ()
    first = proposals[0] if proposals else None
    usage = _usage_records(runtime.delegate) if runtime is not None else []
    runtime_calls = runtime.calls if runtime is not None else 0
    return {
        "condition": condition.value,
        "status": "runtime_failed",
        "runtime_calls": runtime_calls,
        "elapsed_seconds": round(
            runtime.elapsed_seconds if runtime is not None else 0.0,
            6,
        ),
        "usage": usage,
        "usage_summary": summarize_usage(usage, runtime_calls),
        "initial_proposal": (
            _json_clone(first.to_dict()) if first is not None else None
        ),
        "final_proposal": None,
        "initial_score": _score(case, first) if first is not None else None,
        "final_score": None,
        "improved": False,
        "regressed": False,
        "repair_transition": "runtime_failed",
        "error_type": error_type,
        "error_code": error_code if isinstance(error_code, str) else None,
        "error": error,
        "error_details": (
            _json_clone(error_details) if isinstance(error_details, dict) else {}
        ),
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
        "dimensions": _score_dimensions(results),
        "oracle_distance": structural_distance(proposal.result, case.expected_result),
    }


def _evaluation_gates(case: EvaluationCase) -> tuple[Gate, ...]:
    gates: list[Gate] = []
    if "result_spec" in case.contract.inputs:
        gates.append(ResultSpecGate())
    if "semantic_assertions" in case.contract.inputs:
        gates.append(SemanticAssertionsGate())
    if case.domain == "policy_decision":
        gates.extend((PolicyDecisionGate(), PolicyReasonCodeGate()))
    gates.extend(
        (
            ResultEqualsGate(case.expected_result),
            ActionPolicyGate(),
            ClaimEvidenceGate(),
            RequiredEvidenceGate(),
        )
    )
    return tuple(gates)


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
        usage_summary = summarize_usage(
            usage,
            sum(int(item["runtime_calls"]) for item in outcomes),
        )
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
            "unique_cases": len({case["case_id"] for case in case_results}),
            "trials": len(outcomes),
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
            "initial_dimension_passes": _dimension_pass_counts(
                completed,
                "initial_score",
            ),
            "final_dimension_passes": _dimension_pass_counts(
                completed,
                "final_score",
            ),
            "improved": sum(bool(item["improved"]) for item in completed),
            "regressed": sum(bool(item["regressed"]) for item in completed),
            "repair_transitions": {
                transition: sum(item["repair_transition"] == transition for item in outcomes)
                for transition in (
                    "passed_unchanged", "repaired", "partial_improvement",
                    "unchanged_failure", "worsened_failure", "regressed", "runtime_failed"
                )
                if any(item["repair_transition"] == transition for item in outcomes)
            },
            "average_runtime_calls": _average(
                [item["runtime_calls"] for item in outcomes]
            ),
            "average_elapsed_seconds": _average(
                [item["elapsed_seconds"] for item in outcomes]
            ),
            "usage_reported_calls": usage_summary["reported_calls"],
            "reported_total_tokens": usage_summary["reported_total_tokens"],
            "average_reported_total_tokens_per_case": (
                round(usage_summary["reported_total_tokens"] / len(outcomes), 6)
                if usage_summary["reported_calls"]
                else None
            ),
            "usage_missing_calls": usage_summary["missing_calls"],
            "usage_invalid_calls": usage_summary["invalid_calls"],
            "usage_unavailable_calls": usage_summary["unavailable_calls"],
            "usage_unobserved_calls": usage_summary["unobserved_calls"],
            "usage_unexpected_observations": usage_summary[
                "unexpected_observations"
            ],
            "usage_complete": usage_summary["complete"],
        }
    failures = [
        (case, condition, case["conditions"][condition.value])
        for case in case_results
        for condition in EvaluationCondition
        if case["conditions"][condition.value]["status"] == "runtime_failed"
    ]
    summary["runtime_failure_counts"] = {
        "by_code": _sorted_counts(
            item.get("error_code") or "unclassified" for _, _, item in failures
        ),
        "by_condition": _sorted_counts(
            condition.value for _, condition, _ in failures
        ),
        "by_domain": _sorted_counts(case["domain"] for case, _, _ in failures),
        "by_condition_and_code": _sorted_counts(
            f"{condition.value}/{item.get('error_code') or 'unclassified'}"
            for _, condition, item in failures
        ),
    }
    return summary


def _sorted_counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


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


def _score_dimensions(results: tuple[Any, ...]) -> dict[str, bool | None]:
    verdicts = {result.gate: result.passed for result in results}
    dimension_names = (
        "result_spec",
        "semantic_assertions",
        "policy_decision",
        "policy_reason_code",
        "result_equals",
        "action_policy",
        "claim_evidence",
        "required_evidence",
    )
    return {
        name: verdicts.get(name)
        for name in dimension_names
    }


def _dimension_pass_counts(
    outcomes: list[dict[str, Any]],
    score_key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for dimension, passed in outcome[score_key]["dimensions"].items():
            if passed is None:
                continue
            counts.setdefault(dimension, 0)
            if passed:
                counts[dimension] += 1
    return dict(sorted(counts.items()))


def _usage_records(runtime: AgentRuntime) -> list[dict[str, Any]]:
    records = getattr(runtime, "usage_records", ())
    if not isinstance(records, (list, tuple)):
        return []
    return [
        _json_clone(dict(item))
        for item in records
        if isinstance(item, dict)
    ]


def _session_id(
    evaluation_id: str,
    case_id: str,
    repetition: int,
    condition: EvaluationCondition,
) -> str:
    raw = f"{evaluation_id}:{case_id}:{repetition}:{condition.value}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"evaluation:{digest}"


def _trial_sampling_seed(
    base_seed: int,
    case_id: str,
    repetition: int,
) -> int:
    raw = f"{base_seed}:{case_id}:{repetition}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


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


def _snapshot_proposal(proposal: Proposal) -> Proposal:
    if not isinstance(proposal, Proposal):
        raise TypeError("runtime must return a Proposal")
    return Proposal.from_dict(_json_clone(proposal.to_dict()))


def _snapshot_contract(contract: TaskContract) -> TaskContract:
    return TaskContract.from_dict(contract.to_dict())


def _snapshot_feedback(feedback):
    return tuple(
        VerifierFeedback.from_dict(item.to_dict())
        if isinstance(item, VerifierFeedback)
        else item
        for item in feedback
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def structural_distance(actual: Any, expected: Any) -> dict[str, Any]:
    """Return private aggregate mismatch counts without oracle values."""
    counts: dict[str, int] = {}

    def add(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def compare(left: Any, right: Any) -> None:
        left_type, right_type = json_type(left), json_type(right)
        if left_type is None or right_type is None or left_type != right_type:
            add("type_mismatch")
        elif left_type == "object":
            for key in sorted(set(right) - set(left)):
                add("missing_key")
            for key in sorted(set(left) - set(right)):
                add("unexpected_key")
            for key in sorted(set(left) & set(right)):
                compare(left[key], right[key])
        elif left_type == "array":
            for left_item, right_item in zip(left, right):
                compare(left_item, right_item)
            for _ in range(max(0, len(right) - len(left))):
                add("missing_element")
            for _ in range(max(0, len(left) - len(right))):
                add("unexpected_element")
        elif not json_equal(left, right):
            add("value_mismatch")

    compare(actual, expected)
    return {"mismatch_count": sum(counts.values()), "kind_counts": dict(sorted(counts.items()))}


def _repair_transition(initial: dict[str, Any], final: dict[str, Any]) -> str:
    before, after = initial["all_gates_passed"], final["all_gates_passed"]
    if before and after:
        return "passed_unchanged"
    if not before and after:
        return "repaired"
    if before and not after:
        return "regressed"
    initial_distance = initial["oracle_distance"]["mismatch_count"]
    final_distance = final["oracle_distance"]["mismatch_count"]
    if final_distance < initial_distance:
        return "partial_improvement"
    if final_distance > initial_distance:
        return "worsened_failure"
    return "unchanged_failure"
