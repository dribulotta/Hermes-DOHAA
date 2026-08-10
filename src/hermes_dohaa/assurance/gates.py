"""Deterministic gates. No gate may delegate its final verdict to the model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

import json

from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback
from hermes_dohaa.assurance.result_spec import json_equal, parse_result_spec, validate_result


class GateFailureCode(StrEnum):
    RESULT_MISMATCH = "result.mismatch"
    RESULT_SPEC_INVALID = "result.spec_invalid"
    RESULT_KEYS_MISMATCH = "result.keys_mismatch"
    RESULT_TYPE_MISMATCH = "result.type_mismatch"
    RESULT_ENUM_INVALID = "result.enum_invalid"
    POLICY_INPUT_INVALID = "policy.input_invalid"
    POLICY_DECISION_MISMATCH = "policy.decision_mismatch"
    POLICY_REASON_CODE_MISMATCH = "policy.reason_code_mismatch"
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
    details: Any | None = None

    def __post_init__(self) -> None:
        for field_name in ("gate", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"gate result {field_name} must be a non-empty string"
                )
        if not isinstance(self.passed, bool):
            raise ValueError("gate result passed must be a boolean")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.evidence_ids
        ):
            raise ValueError(
                "gate result evidence_ids must contain non-empty strings"
            )
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
        object.__setattr__(self, "details", _json_clone(self.details))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GateResult":
        allowed = {
            "gate",
            "passed",
            "reason",
            "evidence_ids",
            "failure_code",
            "details",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                f"unknown gate result fields: {sorted(unknown)}"
            )
        evidence_ids = raw.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            raise ValueError("gate result evidence_ids must be a list")
        return cls(
            gate=raw.get("gate"),
            passed=raw.get("passed"),
            reason=raw.get("reason"),
            evidence_ids=tuple(evidence_ids),
            failure_code=raw.get("failure_code"),
            details=raw.get("details"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "gate": self.gate,
            "passed": self.passed,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "failure_code": self.failure_code,
        }
        if self.details is not None:
            result["details"] = _json_clone(self.details)
        return result

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
            details=self.details,
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
        if not json_equal(proposal.result, self.expected):
            return GateResult(
                self.name,
                False,
                "Proposal result does not equal the expected value",
                failure_code=GateFailureCode.RESULT_MISMATCH,
            )
        return GateResult(self.name, True, "Proposal result equals the expected value")


@dataclass(frozen=True, slots=True)
class ResultSpecGate:
    """Validate a proposal result against a contract-visible JSON specification."""

    name: str = "result_spec"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        spec = contract.inputs.get("result_spec")
        try:
            parsed = parse_result_spec(spec)
        except ValueError as exc:
            return GateResult(
                self.name,
                False,
                f"Invalid result_spec: {exc}",
                failure_code=GateFailureCode.RESULT_SPEC_INVALID,
            )

        details = validate_result(proposal.result, parsed)
        if details:
            first = details["violations"][0]
            return GateResult(
                self.name, False, "Proposal result does not conform to result_spec",
                failure_code=first["code"], details=details,
            )

        return GateResult(
            self.name,
            True,
            "Proposal result conforms to the declared result_spec",
        )


@dataclass(frozen=True, slots=True)
class PolicyDecisionGate:
    """Validate a hypothetical action decision from contract-visible policy."""

    name: str = "policy_decision"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        try:
            action, expected_decision, _ = _policy_expectation(contract)
        except ValueError as exc:
            return GateResult(
                self.name,
                False,
                f"Invalid policy-decision inputs: {exc}",
                failure_code=GateFailureCode.POLICY_INPUT_INVALID,
            )
        actual = (
            proposal.result.get("decision")
            if isinstance(proposal.result, Mapping)
            else None
        )
        if actual != expected_decision:
            return GateResult(
                self.name,
                False,
                (
                    f"Policy classification for action {action!r} requires "
                    f"decision {expected_decision!r}; received {actual!r}"
                ),
                failure_code=GateFailureCode.POLICY_DECISION_MISMATCH,
            )
        return GateResult(
            self.name,
            True,
            "Proposal decision follows the supplied policy",
        )


@dataclass(frozen=True, slots=True)
class PolicyReasonCodeGate:
    """Validate the stable reason code derived from contract-visible policy."""

    name: str = "policy_reason_code"

    def evaluate(self, contract: TaskContract, proposal: Proposal) -> GateResult:
        try:
            action, _, expected_code = _policy_expectation(contract)
        except ValueError as exc:
            return GateResult(
                self.name,
                False,
                f"Invalid policy-decision inputs: {exc}",
                failure_code=GateFailureCode.POLICY_INPUT_INVALID,
            )
        actual = (
            proposal.result.get("reason_code")
            if isinstance(proposal.result, Mapping)
            else None
        )
        if actual != expected_code:
            return GateResult(
                self.name,
                False,
                (
                    f"Policy classification for action {action!r} requires "
                    f"reason_code {expected_code!r}; received {actual!r}"
                ),
                failure_code=GateFailureCode.POLICY_REASON_CODE_MISMATCH,
            )
        return GateResult(
            self.name,
            True,
            "Proposal reason_code follows the supplied policy",
        )


def _parse_result_spec(
    raw: Any,
) -> tuple[tuple[str, ...], bool, dict[str, str], dict[str, tuple[Any, ...]]]:
    if not isinstance(raw, Mapping):
        raise ValueError("result_spec must be an object")
    allowed_fields = {"required_keys", "additional_keys", "types", "enums"}
    unknown = set(raw) - allowed_fields
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    required_keys = raw.get("required_keys")
    if (
        not isinstance(required_keys, list)
        or not required_keys
        or any(not isinstance(item, str) or not item for item in required_keys)
        or len(required_keys) != len(set(required_keys))
    ):
        raise ValueError("required_keys must be a non-empty list of unique strings")

    allow_additional = raw.get("additional_keys", False)
    if not isinstance(allow_additional, bool):
        raise ValueError("additional_keys must be a boolean")

    raw_types = raw.get("types", {})
    if not isinstance(raw_types, Mapping):
        raise ValueError("types must be an object")
    valid_types = {
        "array", "boolean", "integer", "null", "number", "object", "string"
    }
    types: dict[str, str] = {}
    for field_name, type_name in raw_types.items():
        if field_name not in required_keys:
            raise ValueError(f"type declared for unknown field {field_name!r}")
        if type_name not in valid_types:
            raise ValueError(f"unsupported JSON type {type_name!r}")
        types[field_name] = type_name

    raw_enums = raw.get("enums", {})
    if not isinstance(raw_enums, Mapping):
        raise ValueError("enums must be an object")
    enums: dict[str, tuple[Any, ...]] = {}
    for field_name, values in raw_enums.items():
        if field_name not in required_keys:
            raise ValueError(f"enum declared for unknown field {field_name!r}")
        if not isinstance(values, list) or not values:
            raise ValueError(f"enum for {field_name!r} must be a non-empty list")
        enums[field_name] = tuple(values)

    return tuple(required_keys), allow_additional, types, enums


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, Mapping)
    return False


def _json_clone(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"details must be JSON-serializable: {exc}") from exc


def _policy_expectation(contract: TaskContract) -> tuple[str, str, str]:
    policy = contract.inputs.get("policy")
    request = contract.inputs.get("hypothetical_request")
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be an object")
    if not isinstance(request, Mapping):
        raise ValueError("hypothetical_request must be an object")
    action = request.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError("hypothetical_request.action must be a non-empty string")

    forbidden = policy.get("forbidden_actions", [])
    approval_required = policy.get("approval_required_actions", [])
    allowed = policy.get("allowed_actions", [])
    for label, values in (
        ("forbidden_actions", forbidden),
        ("approval_required_actions", approval_required),
        ("allowed_actions", allowed),
    ):
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise ValueError(f"policy.{label} must be a list of strings")

    classifications = (
        set(forbidden),
        set(approval_required),
        set(allowed),
    )
    if any(
        left & right
        for index, left in enumerate(classifications)
        for right in classifications[index + 1 :]
    ):
        raise ValueError("policy action classifications must not overlap")

    if action in forbidden:
        return action, "deny", "action.forbidden"
    if action in approval_required:
        return action, "escalate", "approval.required"
    if action in allowed:
        return action, "allow", "action.allowed"
    raise ValueError(f"action {action!r} is not classified by policy")
