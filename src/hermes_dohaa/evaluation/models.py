"""Strict schemas for paired comparative evaluation suites."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from hermes_dohaa.contracts.models import ContractError, TaskContract


class EvaluationSuiteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    domain: str
    contract: TaskContract
    expected_result: Any

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvaluationCase":
        _require_exact_fields(
            raw,
            {"case_id", "domain", "contract", "expected_result"},
            "evaluation case",
        )
        contract_raw = raw.get("contract")
        if not isinstance(contract_raw, dict):
            raise EvaluationSuiteError("evaluation case contract must be an object")
        try:
            contract = TaskContract.from_dict(contract_raw)
        except ContractError as exc:
            raise EvaluationSuiteError(f"invalid evaluation contract: {exc}") from exc
        if contract.requires_human_approval:
            raise EvaluationSuiteError(
                "evaluation contracts cannot require human approval"
            )
        if contract.max_attempts != 2:
            raise EvaluationSuiteError(
                "evaluation contracts must use exactly two attempts"
            )
        if "expected_result" in contract.inputs:
            raise EvaluationSuiteError(
                "expected_result is reserved for the hidden evaluation oracle"
            )
        if not isinstance(contract.inputs.get("result_spec"), dict):
            raise EvaluationSuiteError(
                "evaluation contracts must declare inputs.result_spec"
            )
        domain = _required_text(raw, "domain")
        if domain == "policy_decision" and (
            not isinstance(contract.inputs.get("policy"), dict)
            or not isinstance(
                contract.inputs.get("hypothetical_request"),
                dict,
            )
        ):
            raise EvaluationSuiteError(
                "policy_decision cases require policy and hypothetical_request inputs"
            )
        return cls(
            case_id=_required_text(raw, "case_id"),
            domain=domain,
            contract=contract,
            expected_result=_json_clone(raw.get("expected_result")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "domain": self.domain,
            "contract": self.contract.to_dict(),
            "expected_result": _json_clone(self.expected_result),
        }


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    schema_version: str
    suite_id: str
    description: str
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise EvaluationSuiteError(
                f"unsupported evaluation schema: {self.schema_version!r}"
            )
        _require_text_value(self.suite_id, "suite_id")
        _require_text_value(self.description, "description")
        if len(self.cases) < 2:
            raise EvaluationSuiteError(
                "an evaluation suite must contain at least two cases"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationSuiteError("evaluation case IDs must be unique")
        contract_ids = [case.contract.contract_id for case in self.cases]
        if len(contract_ids) != len(set(contract_ids)):
            raise EvaluationSuiteError(
                "evaluation contract IDs must be unique"
            )
        domains = {case.domain for case in self.cases}
        if len(domains) < 2:
            raise EvaluationSuiteError(
                "a comparative suite must contain at least two domains"
            )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvaluationSuite":
        _require_exact_fields(
            raw,
            {"schema_version", "suite_id", "description", "cases"},
            "evaluation suite",
        )
        cases_raw = raw.get("cases")
        if not isinstance(cases_raw, list) or not all(
            isinstance(item, dict) for item in cases_raw
        ):
            raise EvaluationSuiteError(
                "evaluation cases must be a list of objects"
            )
        return cls(
            schema_version=raw.get("schema_version"),
            suite_id=_required_text(raw, "suite_id"),
            description=_required_text(raw, "description"),
            cases=tuple(EvaluationCase.from_dict(item) for item in cases_raw),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "EvaluationSuite":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationSuiteError(
                f"cannot load evaluation suite {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise EvaluationSuiteError("evaluation suite root must be an object")
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "description": self.description,
            "cases": [case.to_dict() for case in self.cases],
        }

    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


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
        raise EvaluationSuiteError(f"value is not canonical JSON: {exc}") from exc


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    _require_text_value(value, key)
    return value.strip()


def _require_text_value(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationSuiteError(f"{field_name} must be a non-empty string")


def _require_exact_fields(
    raw: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown:
        raise EvaluationSuiteError(f"unknown {label} fields: {sorted(unknown)}")
    if missing:
        raise EvaluationSuiteError(f"missing {label} fields: {sorted(missing)}")
