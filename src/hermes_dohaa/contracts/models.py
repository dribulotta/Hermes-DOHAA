"""Strict task contracts owned by the deterministic control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when a task contract violates a control-plane invariant."""


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    required_evidence: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AcceptanceCriterion":
        allowed = {"criterion_id", "description", "required_evidence"}
        unknown = set(raw) - allowed
        if unknown:
            raise ContractError(f"Unknown acceptance-criterion fields: {sorted(unknown)}")
        criterion_id = _required_text(raw, "criterion_id")
        description = _required_text(raw, "description")
        evidence = _string_tuple(raw.get("required_evidence", ()), "required_evidence")
        return cls(criterion_id, description, evidence)


@dataclass(frozen=True, slots=True)
class TaskContract:
    contract_id: str
    objective: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    inputs: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    forbidden_actions: frozenset[str] = field(default_factory=frozenset)
    risk_level: RiskLevel = RiskLevel.LOW
    max_attempts: int = 3
    requires_human_approval: bool = False
    schema_version: str = "1.0"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskContract":
        allowed = {
            "schema_version",
            "contract_id",
            "objective",
            "inputs",
            "constraints",
            "acceptance_criteria",
            "allowed_actions",
            "forbidden_actions",
            "risk_level",
            "max_attempts",
            "requires_human_approval",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ContractError(f"Unknown task-contract fields: {sorted(unknown)}")

        schema_version = str(raw.get("schema_version", "1.0"))
        if schema_version != "1.0":
            raise ContractError(f"Unsupported schema_version: {schema_version!r}")

        criteria_raw = raw.get("acceptance_criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ContractError("acceptance_criteria must be a non-empty list")
        if not all(isinstance(item, dict) for item in criteria_raw):
            raise ContractError("acceptance_criteria must contain only objects")
        criteria = tuple(AcceptanceCriterion.from_dict(item) for item in criteria_raw)
        criterion_ids = [item.criterion_id for item in criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ContractError("acceptance criterion IDs must be unique")

        inputs = raw.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ContractError("inputs must be an object")
        try:
            immutable_inputs = MappingProxyType(
                json.loads(json.dumps(inputs, ensure_ascii=False, sort_keys=True))
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(f"inputs contain a non-JSON value: {exc}") from exc

        try:
            risk_level = RiskLevel(raw.get("risk_level", "low"))
        except ValueError as exc:
            raise ContractError("risk_level must be low, medium, high, or critical") from exc

        max_attempts = raw.get("max_attempts", 3)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ContractError("max_attempts must be an integer")
        if not 1 <= max_attempts <= 10:
            raise ContractError("max_attempts must be between 1 and 10")

        allowed_actions = frozenset(_string_tuple(raw.get("allowed_actions", ()), "allowed_actions"))
        forbidden_actions = frozenset(
            _string_tuple(raw.get("forbidden_actions", ()), "forbidden_actions")
        )
        overlap = allowed_actions & forbidden_actions
        if overlap:
            raise ContractError(f"Actions cannot be both allowed and forbidden: {sorted(overlap)}")

        requires_approval = raw.get("requires_human_approval", False)
        if not isinstance(requires_approval, bool):
            raise ContractError("requires_human_approval must be a boolean")
        if risk_level is RiskLevel.CRITICAL and not requires_approval:
            raise ContractError("critical contracts must require human approval")

        contract = cls(
            schema_version=schema_version,
            contract_id=_required_text(raw, "contract_id"),
            objective=_required_text(raw, "objective"),
            inputs=immutable_inputs,
            constraints=_string_tuple(raw.get("constraints", ()), "constraints"),
            acceptance_criteria=criteria,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            risk_level=risk_level,
            max_attempts=max_attempts,
            requires_human_approval=requires_approval,
        )
        contract.canonical_json()  # Prove that all values are JSON serializable.
        return contract

    @classmethod
    def from_json_file(cls, path: str | Path) -> "TaskContract":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"Cannot load task contract {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ContractError("task contract root must be an object")
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "objective": self.objective,
            "inputs": json.loads(json.dumps(dict(self.inputs), ensure_ascii=False)),
            "constraints": list(self.constraints),
            "acceptance_criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                    "required_evidence": list(criterion.required_evidence),
                }
                for criterion in self.acceptance_criteria
            ],
            "allowed_actions": sorted(self.allowed_actions),
            "forbidden_actions": sorted(self.forbidden_actions),
            "risk_level": self.risk_level.value,
            "max_attempts": self.max_attempts,
            "requires_human_approval": self.requires_human_approval,
        }

    def canonical_json(self) -> str:
        try:
            return json.dumps(
                self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(f"contract contains a non-JSON value: {exc}") from exc


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"{field_name} must be a list of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(f"{field_name} must contain only non-empty strings")
    return tuple(item.strip() for item in value)
