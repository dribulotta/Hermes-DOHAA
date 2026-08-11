"""Strict, canonical preregistration for multi-model evaluations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


_CONDITIONS = ("direct", "self_reflection", "dohaa")
_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}


class EvaluationProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelSlot:
    slot_id: str
    selection_rule: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelSlot":
        _require_exact_fields(
            raw,
            {"slot_id", "selection_rule"},
            "model slot",
        )
        return cls(
            slot_id=_required_text(raw, "slot_id"),
            selection_rule=_required_text(raw, "selection_rule"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "slot_id": self.slot_id,
            "selection_rule": self.selection_rule,
        }


@dataclass(frozen=True, slots=True)
class EvaluationProtocol:
    schema_version: str
    protocol_id: str
    description: str
    status: str
    conditions: tuple[str, ...]
    model_slots: tuple[ModelSlot, ...]
    model_policy: Mapping[str, Any]
    suite_policy: Mapping[str, Any]
    execution_policy: Mapping[str, Any]
    analysis_plan: Mapping[str, Any]
    success_criteria: Mapping[str, Any]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvaluationProtocol":
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "protocol_id",
                "description",
                "status",
                "conditions",
                "model_slots",
                "model_policy",
                "suite_policy",
                "execution_policy",
                "analysis_plan",
                "success_criteria",
            },
            "evaluation protocol",
        )
        if raw.get("schema_version") != "1.0":
            raise EvaluationProtocolError(
                "unsupported evaluation protocol schema"
            )
        if raw.get("status") != "preregistered":
            raise EvaluationProtocolError(
                "evaluation protocol status must be preregistered"
            )

        conditions_raw = raw.get("conditions")
        if not isinstance(conditions_raw, list) or tuple(conditions_raw) != _CONDITIONS:
            raise EvaluationProtocolError(
                "conditions must be direct, self_reflection, and dohaa in that order"
            )

        slots_raw = raw.get("model_slots")
        if not isinstance(slots_raw, list) or not all(
            isinstance(item, dict) for item in slots_raw
        ):
            raise EvaluationProtocolError("model_slots must be a list of objects")
        slots = tuple(ModelSlot.from_dict(item) for item in slots_raw)
        slot_ids = [slot.slot_id for slot in slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise EvaluationProtocolError("model slot IDs must be unique")

        model_policy = _required_object(raw, "model_policy")
        suite_policy = _required_object(raw, "suite_policy")
        execution_policy = _required_object(raw, "execution_policy")
        analysis_plan = _required_object(raw, "analysis_plan")
        success_criteria = _required_object(raw, "success_criteria")

        _validate_model_policy(model_policy, len(slots))
        _validate_suite_policy(suite_policy)
        _validate_execution_policy(execution_policy)
        _validate_analysis_plan(analysis_plan)
        _validate_success_criteria(success_criteria, len(slots))

        return cls(
            schema_version="1.0",
            protocol_id=_required_text(raw, "protocol_id"),
            description=_required_text(raw, "description"),
            status="preregistered",
            conditions=_CONDITIONS,
            model_slots=slots,
            model_policy=_frozen_json_object(model_policy),
            suite_policy=_frozen_json_object(suite_policy),
            execution_policy=_frozen_json_object(execution_policy),
            analysis_plan=_frozen_json_object(analysis_plan),
            success_criteria=_frozen_json_object(success_criteria),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "EvaluationProtocol":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationProtocolError(
                f"cannot load evaluation protocol {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise EvaluationProtocolError(
                "evaluation protocol root must be an object"
            )
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_id": self.protocol_id,
            "description": self.description,
            "status": self.status,
            "conditions": list(self.conditions),
            "model_slots": [slot.to_dict() for slot in self.model_slots],
            "model_policy": _thaw_json(self.model_policy),
            "suite_policy": _thaw_json(self.suite_policy),
            "execution_policy": _thaw_json(self.execution_policy),
            "analysis_plan": _thaw_json(self.analysis_plan),
            "success_criteria": _thaw_json(self.success_criteria),
        }

    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


def _validate_model_policy(raw: Mapping[str, Any], slot_count: int) -> None:
    _require_exact_fields(
        raw,
        {
            "model_count",
            "artifact_identity_required",
            "freeze_before_suite_authorship",
            "allow_post_freeze_substitution",
        },
        "model policy",
    )
    model_count = _required_int(raw, "model_count", minimum=3, maximum=20)
    if model_count != slot_count:
        raise EvaluationProtocolError(
            "model_count must equal the number of model_slots"
        )
    _require_boolean_value(raw, "artifact_identity_required", True)
    _require_boolean_value(raw, "freeze_before_suite_authorship", True)
    _require_boolean_value(raw, "allow_post_freeze_substitution", False)


def _validate_suite_policy(raw: Mapping[str, Any]) -> None:
    _require_exact_fields(
        raw,
        {
            "case_count",
            "domain_counts",
            "new_cases_only",
            "reuse_prior_holdouts",
            "authoring_after_model_freeze",
        },
        "suite policy",
    )
    case_count = _required_int(raw, "case_count", minimum=30, maximum=50)
    domain_counts_raw = raw.get("domain_counts")
    if not isinstance(domain_counts_raw, dict) or len(domain_counts_raw) < 3:
        raise EvaluationProtocolError(
            "domain_counts must contain at least three domains"
        )
    domain_counts: dict[str, int] = {}
    for domain, count in domain_counts_raw.items():
        if not isinstance(domain, str) or not domain.strip():
            raise EvaluationProtocolError(
                "domain_counts keys must be non-empty strings"
            )
        if isinstance(count, bool) or not isinstance(count, int) or count < 5:
            raise EvaluationProtocolError(
                "every preregistered domain must contain at least five cases"
            )
        domain_counts[domain] = count
    if sum(domain_counts.values()) != case_count:
        raise EvaluationProtocolError(
            "case_count must equal the sum of domain_counts"
        )
    _require_boolean_value(raw, "new_cases_only", True)
    _require_boolean_value(raw, "reuse_prior_holdouts", False)
    _require_boolean_value(raw, "authoring_after_model_freeze", True)


def _validate_execution_policy(raw: Mapping[str, Any]) -> None:
    _require_exact_fields(
        raw,
        {
            "repetitions",
            "condition_order_seed",
            "sampling_seed",
            "temperature",
            "top_p",
            "reasoning_effort",
            "timeout_seconds",
        },
        "execution policy",
    )
    _required_int(raw, "repetitions", minimum=1, maximum=100)
    _required_int(raw, "condition_order_seed", minimum=0, maximum=2**63 - 1)
    _required_int(raw, "sampling_seed", minimum=0, maximum=2**63 - 1)
    temperature = _required_number(raw, "temperature")
    if not 0.0 <= temperature <= 2.0:
        raise EvaluationProtocolError("temperature must be between 0 and 2")
    top_p = _required_number(raw, "top_p")
    if not 0.0 < top_p <= 1.0:
        raise EvaluationProtocolError("top_p must be greater than 0 and at most 1")
    if raw.get("reasoning_effort") not in _REASONING_EFFORTS:
        raise EvaluationProtocolError("unsupported reasoning_effort")
    timeout = _required_number(raw, "timeout_seconds")
    if not 0.0 < timeout <= 3600.0:
        raise EvaluationProtocolError(
            "timeout_seconds must be greater than 0 and at most 3600"
        )


def _validate_analysis_plan(raw: Mapping[str, Any]) -> None:
    _require_exact_fields(
        raw,
        {
            "primary_comparison",
            "primary_metric",
            "global_unit",
            "global_aggregation",
            "per_model_test",
            "alpha",
            "domain_analysis",
            "multiple_comparison_claims",
            "runtime_failures",
        },
        "analysis plan",
    )
    expected = {
        "primary_comparison": "dohaa_vs_direct",
        "primary_metric": "final_strict_pass",
        "global_unit": "unique_case",
        "global_aggregation": "mean_across_models_then_sign",
        "per_model_test": "exact_two_sided_sign_test",
        "domain_analysis": "exploratory",
        "runtime_failures": "count_as_failures",
    }
    for key, value in expected.items():
        if raw.get(key) != value:
            raise EvaluationProtocolError(f"{key} must be {value}")
    alpha = _required_number(raw, "alpha")
    if not 0.0 < alpha < 1.0:
        raise EvaluationProtocolError("alpha must be between 0 and 1")
    _require_boolean_value(raw, "multiple_comparison_claims", False)


def _validate_success_criteria(raw: Mapping[str, Any], model_count: int) -> None:
    _require_exact_fields(
        raw,
        {
            "minimum_models_with_positive_delta",
            "maximum_models_with_negative_delta",
            "require_global_positive_delta",
            "require_global_wins_exceed_losses",
            "require_primary_p_below_alpha",
            "maximum_regressions",
            "maximum_dohaa_average_runtime_calls",
            "maximum_dohaa_to_direct_token_ratio",
        },
        "success criteria",
    )
    _required_int(
        raw,
        "minimum_models_with_positive_delta",
        minimum=1,
        maximum=model_count,
    )
    _required_int(
        raw,
        "maximum_models_with_negative_delta",
        minimum=0,
        maximum=model_count,
    )
    _require_boolean_value(raw, "require_global_positive_delta", True)
    _require_boolean_value(raw, "require_global_wins_exceed_losses", True)
    _require_boolean_value(raw, "require_primary_p_below_alpha", True)
    _required_int(raw, "maximum_regressions", minimum=0, maximum=10_000)
    if _required_number(raw, "maximum_dohaa_average_runtime_calls") < 1.0:
        raise EvaluationProtocolError(
            "maximum_dohaa_average_runtime_calls must be at least 1"
        )
    if _required_number(raw, "maximum_dohaa_to_direct_token_ratio") < 1.0:
        raise EvaluationProtocolError(
            "maximum_dohaa_to_direct_token_ratio must be at least 1"
        )


def _required_object(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise EvaluationProtocolError(f"{key} must be an object")
    return value


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationProtocolError(f"{key} must be a non-empty string")
    return value.strip()


def _required_int(
    raw: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationProtocolError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise EvaluationProtocolError(
            f"{key} must be between {minimum} and {maximum}"
        )
    return value


def _required_number(raw: Mapping[str, Any], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationProtocolError(f"{key} must be a number")
    return float(value)


def _require_boolean_value(
    raw: Mapping[str, Any],
    key: str,
    expected: bool,
) -> None:
    value = raw.get(key)
    if not isinstance(value, bool) or value is not expected:
        raise EvaluationProtocolError(f"{key} must be {str(expected).lower()}")


def _require_exact_fields(
    raw: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown:
        raise EvaluationProtocolError(f"unknown {label} fields: {sorted(unknown)}")
    if missing:
        raise EvaluationProtocolError(f"missing {label} fields: {sorted(missing)}")


def _frozen_json_object(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_json(_json_clone(dict(raw)))
    if not isinstance(frozen, Mapping):
        raise EvaluationProtocolError("expected a JSON object")
    return frozen


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvaluationProtocolError(
            f"value is not canonical JSON: {exc}"
        ) from exc


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))
