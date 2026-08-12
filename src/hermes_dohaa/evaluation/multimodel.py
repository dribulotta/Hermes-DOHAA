"""Preregistered multi-model execution and unique-case aggregation."""

from __future__ import annotations

import json
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from .commitment import SuiteCommitment, SuiteCommitmentError
from .model_manifest import ModelArtifact, ModelManifest, ModelManifestError
from .models import EvaluationSuite
from .protocol import EvaluationProtocol
from .runner import RuntimeFactory, run_comparative_evaluation
from .statistics import exact_two_sided_sign_test_p


class MultimodelEvaluationError(ValueError):
    pass


RuntimeFactoryBuilder = Callable[[ModelArtifact], RuntimeFactory]
_GIT_SHA1 = re.compile(r"[0-9a-f]{40}")
_CHECKPOINT_FIELDS = {
    "schema_version", "checkpoint_type", "checkpoint_id", "status",
    "execution_mode", "execution_code_commit", "protocol_id",
    "protocol_sha256", "model_manifest_id", "model_manifest_sha256",
    "suite_id", "suite_sha256", "suite_commitment_id",
    "suite_commitment_sha256", "slot_id", "model_alias",
    "model_artifact_id", "started_at", "completed_at", "runtime_policy",
    "evaluation",
}


def run_multimodel_evaluation(
    suite: EvaluationSuite,
    suite_commitment: SuiteCommitment,
    protocol: EvaluationProtocol,
    model_manifest: ModelManifest,
    runtime_factory_builder: RuntimeFactoryBuilder,
    *,
    runtime_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run every frozen model under the protocol's immutable policy."""
    validate_multimodel_inputs(
        suite,
        suite_commitment,
        protocol,
        model_manifest,
    )
    execution = protocol.execution_policy
    context = _json_clone(dict(runtime_context or {}))
    started_at = _utc_now()
    model_runs: list[dict[str, Any]] = []

    for model in model_manifest.models:
        runtime_factory = runtime_factory_builder(model)
        runtime_policy = _runtime_policy(model, execution, context)
        evaluation = run_comparative_evaluation(
            suite,
            runtime_factory,
            seed=execution["condition_order_seed"],
            repetitions=execution["repetitions"],
            sampling_seed=execution["sampling_seed"],
            runtime_policy=runtime_policy,
            suite_commitment=suite_commitment.to_dict(),
        )
        model_runs.append(
            {
                "slot_id": model.slot_id,
                "model_alias": model.model_alias,
                "model_artifact_id": model.model_artifact_id,
                "evaluation": evaluation,
            }
        )

    aggregate = analyze_multimodel_results(protocol, model_runs)
    return {
        "schema_version": "1.0",
        "evaluation_id": str(uuid4()),
        "status": "completed",
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.sha256(),
        "model_manifest": model_manifest.to_dict(),
        "model_manifest_sha256": model_manifest.sha256(),
        "suite_id": suite.suite_id,
        "suite_sha256": suite.sha256(),
        "suite_commitment": suite_commitment.to_dict(),
        "suite_commitment_sha256": suite_commitment.sha256(),
        "started_at": started_at,
        "completed_at": _utc_now(),
        "model_runs": model_runs,
        "aggregate_analysis": aggregate,
        "success_assessment": assess_success(protocol, model_runs, aggregate),
    }


def run_model_slot_evaluation(
    suite: EvaluationSuite,
    suite_commitment: SuiteCommitment,
    protocol: EvaluationProtocol,
    model_manifest: ModelManifest,
    slot_id: str,
    runtime_factory_builder: RuntimeFactoryBuilder,
    *,
    runtime_context: Mapping[str, Any] | None = None,
    execution_code_commit: str,
) -> dict[str, Any]:
    """Run one validated model slot and return a self-verifying checkpoint."""
    validate_multimodel_inputs(suite, suite_commitment, protocol, model_manifest)
    commit = _execution_commit(execution_code_commit)
    models = [model for model in model_manifest.models if model.slot_id == slot_id]
    if len(models) != 1:
        raise MultimodelEvaluationError(f"unknown model slot: {slot_id}")
    model = models[0]
    execution = protocol.execution_policy
    policy = _runtime_policy(
        model, execution, _json_clone(dict(runtime_context or {}))
    )
    started_at = _utc_now()
    evaluation = run_comparative_evaluation(
        suite,
        runtime_factory_builder(model),
        seed=execution["condition_order_seed"],
        repetitions=execution["repetitions"],
        sampling_seed=execution["sampling_seed"],
        runtime_policy=policy,
        suite_commitment=suite_commitment.to_dict(),
    )
    checkpoint = {
        "schema_version": "1.0",
        "checkpoint_type": "multimodel_model_slot",
        "status": "completed",
        "execution_mode": "isolated_model_slot",
        "execution_code_commit": commit,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.sha256(),
        "model_manifest_id": model_manifest.manifest_id,
        "model_manifest_sha256": model_manifest.sha256(),
        "suite_id": suite.suite_id,
        "suite_sha256": suite.sha256(),
        "suite_commitment_id": suite_commitment.commitment_id,
        "suite_commitment_sha256": suite_commitment.sha256(),
        "slot_id": model.slot_id,
        "model_alias": model.model_alias,
        "model_artifact_id": model.model_artifact_id,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "runtime_policy": policy,
        "evaluation": evaluation,
    }
    checkpoint = _json_clone(checkpoint)
    checkpoint["checkpoint_id"] = _checkpoint_identity(checkpoint)
    return _json_clone(checkpoint)


def aggregate_model_slot_checkpoints(
    suite: EvaluationSuite,
    suite_commitment: SuiteCommitment,
    protocol: EvaluationProtocol,
    model_manifest: ModelManifest,
    checkpoints: list[Mapping[str, Any]],
    *,
    execution_code_commit: str,
) -> dict[str, Any]:
    """Verify and aggregate completed slots without accessing a runtime."""
    validate_multimodel_inputs(suite, suite_commitment, protocol, model_manifest)
    commit = _execution_commit(execution_code_commit)
    expected_slots = [slot.slot_id for slot in protocol.model_slots]
    if len(checkpoints) != len(expected_slots):
        raise MultimodelEvaluationError(
            "checkpoint count does not match the protocol slots"
        )
    canonical = [_canonical_checkpoint(item) for item in checkpoints]
    actual_slots = [item.get("slot_id") for item in canonical]
    if actual_slots != expected_slots:
        if len(actual_slots) != len(set(actual_slots)):
            raise MultimodelEvaluationError("duplicate model-slot checkpoint")
        raise MultimodelEvaluationError(
            "checkpoints must match the protocol slot order"
        )

    model_runs = []
    sources = []
    for checkpoint, model in zip(canonical, model_manifest.models):
        expected = {
            "schema_version": "1.0",
            "checkpoint_type": "multimodel_model_slot",
            "status": "completed",
            "execution_mode": "isolated_model_slot",
            "execution_code_commit": commit,
            "protocol_id": protocol.protocol_id,
            "protocol_sha256": protocol.sha256(),
            "model_manifest_id": model_manifest.manifest_id,
            "model_manifest_sha256": model_manifest.sha256(),
            "suite_id": suite.suite_id,
            "suite_sha256": suite.sha256(),
            "suite_commitment_id": suite_commitment.commitment_id,
            "suite_commitment_sha256": suite_commitment.sha256(),
            "slot_id": model.slot_id,
            "model_alias": model.model_alias,
            "model_artifact_id": model.model_artifact_id,
        }
        mismatches = [key for key, value in expected.items()
                      if checkpoint.get(key) != value]
        policy = _runtime_policy(model, protocol.execution_policy,
                                 _runtime_context(checkpoint["runtime_policy"]))
        if checkpoint["runtime_policy"] != policy:
            mismatches.append("runtime_policy")
        if checkpoint["checkpoint_id"] != _checkpoint_identity(checkpoint):
            mismatches.append("checkpoint_id")
        if mismatches:
            raise MultimodelEvaluationError(
                "checkpoint identity mismatch: " + ", ".join(mismatches)
            )
        model_runs.append({
            "slot_id": model.slot_id,
            "model_alias": model.model_alias,
            "model_artifact_id": model.model_artifact_id,
            "evaluation": _json_clone(checkpoint["evaluation"]),
        })
        sources.append({
            "slot_id": model.slot_id,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_sha256": _sha256(checkpoint),
        })
    aggregate = analyze_multimodel_results(protocol, model_runs)
    return {
        "schema_version": "1.0",
        "evaluation_id": str(uuid4()),
        "status": "completed",
        "execution_mode": "isolated_model_slots",
        "execution_code_commit": commit,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.sha256(),
        "model_manifest": model_manifest.to_dict(),
        "model_manifest_sha256": model_manifest.sha256(),
        "suite_id": suite.suite_id,
        "suite_sha256": suite.sha256(),
        "suite_commitment": suite_commitment.to_dict(),
        "suite_commitment_sha256": suite_commitment.sha256(),
        "started_at": min(
            canonical, key=lambda item: datetime.fromisoformat(item["started_at"])
        )["started_at"],
        "completed_at": max(
            canonical, key=lambda item: datetime.fromisoformat(item["completed_at"])
        )["completed_at"],
        "model_runs": model_runs,
        "aggregate_analysis": aggregate,
        "success_assessment": assess_success(protocol, model_runs, aggregate),
        "source_checkpoints": sources,
    }


def validate_multimodel_inputs(
    suite: EvaluationSuite,
    suite_commitment: SuiteCommitment,
    protocol: EvaluationProtocol,
    model_manifest: ModelManifest,
) -> None:
    """Fail before runtime access when any frozen identity or shape differs."""
    try:
        model_manifest.verify(protocol)
        suite_commitment.verify(suite)
    except (ModelManifestError, SuiteCommitmentError) as exc:
        raise MultimodelEvaluationError(str(exc)) from exc

    actual_domains: dict[str, int] = {}
    for case in suite.cases:
        actual_domains[case.domain] = actual_domains.get(case.domain, 0) + 1
    expected_domains = dict(protocol.suite_policy["domain_counts"])
    if len(suite.cases) != protocol.suite_policy["case_count"]:
        raise MultimodelEvaluationError(
            "suite case count does not match the preregistered protocol"
        )
    if dict(sorted(actual_domains.items())) != dict(sorted(expected_domains.items())):
        raise MultimodelEvaluationError(
            "suite domain counts do not match the preregistered protocol"
        )
    manifest_time = datetime.fromisoformat(model_manifest.frozen_at)
    suite_time = datetime.fromisoformat(suite_commitment.frozen_at)
    if suite_time <= manifest_time:
        raise MultimodelEvaluationError(
            "suite commitment must be frozen after the model manifest"
        )


def analyze_multimodel_results(
    protocol: EvaluationProtocol,
    model_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(model_runs) != len(protocol.model_slots):
        raise MultimodelEvaluationError(
            "model result count does not match the protocol"
        )
    expected_slots = [slot.slot_id for slot in protocol.model_slots]
    actual_slots = [str(run.get("slot_id")) for run in model_runs]
    if actual_slots != expected_slots:
        raise MultimodelEvaluationError(
            "model results must match the protocol slot order"
        )

    rates_by_model: dict[str, dict[str, dict[str, float]]] = {}
    per_model: dict[str, Any] = {}
    expected_cases: set[str] | None = None
    expected_domains: dict[str, str] | None = None
    for run in model_runs:
        slot_id = str(run["slot_id"])
        evaluation = _evaluation(run)
        rates = {
            condition: _case_rates(evaluation, condition)
            for condition in ("direct", "self_reflection", "dohaa")
        }
        case_ids = set(rates["direct"])
        domains = _case_domains(evaluation)
        if expected_cases is None:
            expected_cases = case_ids
            expected_domains = domains
        if case_ids != expected_cases or any(
            set(values) != case_ids for values in rates.values()
        ):
            raise MultimodelEvaluationError(
                "every model and condition must contain the same unique cases"
            )
        if domains != expected_domains:
            raise MultimodelEvaluationError(
                "every model result must preserve the same case domains"
            )
        rates_by_model[slot_id] = rates
        paired = _paired(rates["dohaa"], rates["direct"])
        per_model[slot_id] = {
            "model_alias": run.get("model_alias"),
            "model_artifact_id": run.get("model_artifact_id"),
            "unique_cases": len(case_ids),
            "direct_final_pass_rate": _mean(rates["direct"].values()),
            "dohaa_final_pass_rate": _mean(rates["dohaa"].values()),
            "dohaa_vs_direct": paired,
        }

    if not expected_cases:
        raise MultimodelEvaluationError(
            "multi-model analysis requires at least one unique case"
        )
    mean_rates: dict[str, dict[str, float]] = {
        condition: {
            case_id: _mean(
                rates_by_model[slot][condition][case_id]
                for slot in expected_slots
            )
            for case_id in sorted(expected_cases)
        }
        for condition in ("direct", "self_reflection", "dohaa")
    }
    global_pair = _paired(mean_rates["dohaa"], mean_rates["direct"])
    domain_statistics = {}
    for domain in sorted(set((expected_domains or {}).values())):
        domain_cases = sorted(
            case_id
            for case_id, case_domain in (expected_domains or {}).items()
            if case_domain == domain
        )
        domain_rates = {
            condition: {
                case_id: mean_rates[condition][case_id]
                for case_id in domain_cases
            }
            for condition in mean_rates
        }
        domain_statistics[domain] = {
            "exploratory": True,
            "unique_cases": len(domain_cases),
            "condition_mean_final_pass_rates": {
                condition: _mean(values.values())
                for condition, values in domain_rates.items()
            },
            "dohaa_vs_direct": _paired(
                domain_rates["dohaa"], domain_rates["direct"]
            ),
        }
    return {
        "unit_of_analysis": "unique_case",
        "global_aggregation": "mean_across_models_then_sign",
        "model_count": len(model_runs),
        "unique_cases": len(expected_cases),
        "condition_mean_final_pass_rates": {
            condition: _mean(values.values())
            for condition, values in mean_rates.items()
        },
        "primary_comparison": {
            "name": "dohaa_vs_direct",
            **global_pair,
        },
        "per_model": per_model,
        "domain_statistics": domain_statistics,
        "domain_statistics_note": (
            "Exploratory only: no correction for multiple comparisons is applied."
        ),
        "interpretation": (
            "Each unique case is one global unit. Condition pass rates are "
            "averaged across frozen models before the paired sign test."
        ),
    }


def assess_success(
    protocol: EvaluationProtocol,
    model_runs: list[dict[str, Any]],
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    criteria = protocol.success_criteria
    per_model = aggregate["per_model"]
    deltas = {
        slot: values["dohaa_vs_direct"]["mean_pass_rate_difference"]
        for slot, values in per_model.items()
    }
    positive_models = sum(value > 0 for value in deltas.values())
    negative_models = sum(value < 0 for value in deltas.values())
    primary = aggregate["primary_comparison"]
    regressions = sum(
        int(_evaluation(run)["summary"]["dohaa"]["regressed"])
        for run in model_runs
    )
    dohaa_calls, dohaa_trials = _calls_and_trials(model_runs, "dohaa")
    average_dohaa_calls = round(dohaa_calls / dohaa_trials, 6)
    direct_tokens, direct_usage_calls, direct_calls = _token_totals(
        model_runs, "direct"
    )
    dohaa_tokens, dohaa_usage_calls, dohaa_total_calls = _token_totals(
        model_runs, "dohaa"
    )
    token_complete = (
        direct_calls > 0
        and dohaa_total_calls > 0
        and direct_usage_calls == direct_calls
        and dohaa_usage_calls == dohaa_total_calls
        and direct_tokens > 0
    )
    token_ratio = (
        round(dohaa_tokens / direct_tokens, 6) if token_complete else None
    )
    alpha = protocol.analysis_plan["alpha"]
    primary_p = primary["exact_two_sided_sign_test_p"]

    results = [
        _criterion(
            "minimum_models_with_positive_delta",
            positive_models >= criteria["minimum_models_with_positive_delta"],
            positive_models,
            criteria["minimum_models_with_positive_delta"],
        ),
        _criterion(
            "maximum_models_with_negative_delta",
            negative_models <= criteria["maximum_models_with_negative_delta"],
            negative_models,
            criteria["maximum_models_with_negative_delta"],
        ),
        _criterion(
            "global_positive_delta",
            primary["mean_pass_rate_difference"] > 0,
            primary["mean_pass_rate_difference"],
            "> 0",
        ),
        _criterion(
            "global_wins_exceed_losses",
            primary["wins"] > primary["losses"],
            {"wins": primary["wins"], "losses": primary["losses"]},
            "wins > losses",
        ),
        _criterion(
            "primary_p_below_alpha",
            primary_p is not None and primary_p < alpha,
            primary_p,
            f"< {alpha}",
            evaluable=primary_p is not None,
        ),
        _criterion(
            "maximum_regressions",
            regressions <= criteria["maximum_regressions"],
            regressions,
            criteria["maximum_regressions"],
        ),
        _criterion(
            "maximum_dohaa_average_runtime_calls",
            average_dohaa_calls
            <= criteria["maximum_dohaa_average_runtime_calls"],
            average_dohaa_calls,
            criteria["maximum_dohaa_average_runtime_calls"],
        ),
        _criterion(
            "maximum_dohaa_to_direct_token_ratio",
            token_ratio is not None
            and token_ratio
            <= criteria["maximum_dohaa_to_direct_token_ratio"],
            token_ratio,
            criteria["maximum_dohaa_to_direct_token_ratio"],
            evaluable=token_complete,
        ),
    ]
    failed = [item["criterion"] for item in results if item["status"] == "failed"]
    unevaluable = [
        item["criterion"] for item in results if item["status"] == "unevaluable"
    ]
    return {
        "passed": not failed and not unevaluable,
        "status": "passed" if not failed and not unevaluable else "not_passed",
        "criteria": results,
        "failed_criteria": failed,
        "unevaluable_criteria": unevaluable,
        "model_pass_rate_deltas": dict(sorted(deltas.items())),
        "token_usage_complete": token_complete,
    }


def _criterion(
    name: str,
    passed: bool,
    observed: Any,
    threshold: Any,
    *,
    evaluable: bool = True,
) -> dict[str, Any]:
    return {
        "criterion": name,
        "status": "passed" if passed else ("failed" if evaluable else "unevaluable"),
        "observed": observed,
        "threshold": threshold,
    }


def _evaluation(run: Mapping[str, Any]) -> dict[str, Any]:
    value = run.get("evaluation")
    if not isinstance(value, dict):
        raise MultimodelEvaluationError("model run evaluation must be an object")
    return value


def _case_rates(evaluation: Mapping[str, Any], condition: str) -> dict[str, float]:
    trials = evaluation.get("cases")
    if not isinstance(trials, list) or not trials:
        raise MultimodelEvaluationError("model evaluation contains no cases")
    grouped: dict[str, list[int]] = defaultdict(list)
    for trial in trials:
        try:
            outcome = trial["conditions"][condition]
            score = outcome.get("final_score")
            passed = int(bool(score and score.get("all_gates_passed")))
            grouped[str(trial["case_id"])].append(passed)
        except (KeyError, TypeError, AttributeError) as exc:
            raise MultimodelEvaluationError(
                "model evaluation has an invalid case outcome"
            ) from exc
    return {
        case_id: sum(values) / len(values)
        for case_id, values in sorted(grouped.items())
    }


def _case_domains(evaluation: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for trial in evaluation["cases"]:
        case_id = str(trial["case_id"])
        domain = str(trial["domain"])
        existing = result.setdefault(case_id, domain)
        if existing != domain:
            raise MultimodelEvaluationError(
                "a unique case cannot change domains between repetitions"
            )
    return dict(sorted(result.items()))


def _paired(left: Mapping[str, float], right: Mapping[str, float]) -> dict[str, Any]:
    if set(left) != set(right):
        raise MultimodelEvaluationError("paired rates must contain the same cases")
    wins = sum(left[key] > right[key] for key in left)
    losses = sum(left[key] < right[key] for key in left)
    ties = len(left) - wins - losses
    differences = [left[key] - right[key] for key in sorted(left)]
    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "discordant_cases": wins + losses,
        "mean_pass_rate_difference": _mean(differences),
        "exact_two_sided_sign_test_p": exact_two_sided_sign_test_p(
            wins, losses
        ),
    }


def _calls_and_trials(
    model_runs: list[dict[str, Any]], condition: str
) -> tuple[int, int]:
    calls = 0
    trials = 0
    for run in model_runs:
        for case in _evaluation(run)["cases"]:
            calls += int(case["conditions"][condition]["runtime_calls"])
            trials += 1
    if trials == 0:
        raise MultimodelEvaluationError("runtime-call analysis requires trials")
    return calls, trials


def _token_totals(
    model_runs: list[dict[str, Any]], condition: str
) -> tuple[int, int, int]:
    tokens = 0
    usage_calls = 0
    calls = 0
    for run in model_runs:
        for case in _evaluation(run)["cases"]:
            outcome = case["conditions"][condition]
            calls += int(outcome["runtime_calls"])
            for usage in outcome.get("usage", []):
                total = usage.get("total_tokens") if isinstance(usage, dict) else None
                if not isinstance(total, bool) and isinstance(total, (int, float)):
                    tokens += int(total)
                    usage_calls += 1
    return tokens, usage_calls, calls


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        raise MultimodelEvaluationError("cannot average an empty collection")
    return round(sum(items) / len(items), 6)


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise MultimodelEvaluationError(
            f"runtime context must be canonical JSON: {exc}"
        ) from exc


def _runtime_policy(
    model: ModelArtifact,
    execution: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    return _json_clone({
        **context,
        "model_slot": model.slot_id,
        "model_alias": model.model_alias,
        "model_artifact_id": model.model_artifact_id,
        "provider": model.provider,
        "backend": model.backend,
        "backend_version": model.backend_version,
        "architecture": dict(model.architecture),
        "context_length": model.context_length,
        "quantization": model.quantization,
        "server_config_sha256": model.server_config_sha256,
        "reasoning_effort": execution["reasoning_effort"],
        "temperature": execution["temperature"],
        "top_p": execution["top_p"],
        "sampling_seed": execution["sampling_seed"],
        "timeout_seconds": execution["timeout_seconds"],
    })


def _runtime_context(policy: Mapping[str, Any]) -> dict[str, Any]:
    reserved = {
        "model_slot", "model_alias", "model_artifact_id", "provider",
        "backend", "backend_version", "architecture", "context_length",
        "quantization", "server_config_sha256", "reasoning_effort",
        "temperature", "top_p", "sampling_seed", "timeout_seconds",
    }
    return {key: _json_clone(value) for key, value in policy.items()
            if key not in reserved}


def _execution_commit(value: str) -> str:
    if not isinstance(value, str) or _GIT_SHA1.fullmatch(value) is None:
        raise MultimodelEvaluationError(
            "execution_code_commit must be a full lowercase 40-character Git SHA-1"
        )
    return value


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _checkpoint_identity(checkpoint: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in checkpoint.items()
                    if key != "checkpoint_id"})


def _canonical_checkpoint(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _CHECKPOINT_FIELDS:
        raise MultimodelEvaluationError(
            "checkpoint must be a complete canonical JSON object"
        )
    clone = _json_clone(value)
    if clone != value:
        raise MultimodelEvaluationError("checkpoint content is not canonical JSON")
    for field in ("runtime_policy", "evaluation"):
        if type(clone[field]) is not dict:
            raise MultimodelEvaluationError(f"checkpoint {field} must be an object")
    for field in ("started_at", "completed_at"):
        try:
            parsed = datetime.fromisoformat(clone[field])
        except (TypeError, ValueError) as exc:
            raise MultimodelEvaluationError(
                f"checkpoint {field} must be an ISO-8601 timestamp"
            ) from exc
        if parsed.tzinfo is None:
            raise MultimodelEvaluationError(
                f"checkpoint {field} must include a timezone"
            )
    if datetime.fromisoformat(clone["completed_at"]) < datetime.fromisoformat(
        clone["started_at"]
    ):
        raise MultimodelEvaluationError(
            "checkpoint completed_at cannot precede started_at"
        )
    return clone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
