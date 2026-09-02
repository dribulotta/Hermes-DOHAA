"""Runtime-neutral proposal types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from hermes_dohaa.contracts.models import TaskContract


class RuleAwareRepairUnavailableError(RuntimeError):
    """Raised when an opt-in scoped retry reaches an incapable runtime."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    source: str
    content: Any
    sha256: str

    @classmethod
    def create(cls, evidence_id: str, kind: str, source: str, content: Any) -> "EvidenceItem":
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(evidence_id, kind, source, content, digest)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceItem":
        item = cls.create(
            _text(raw, "evidence_id"),
            _text(raw, "kind"),
            _text(raw, "source"),
            raw.get("content"),
        )
        provided_sha256 = raw.get("sha256")
        if provided_sha256 is not None and provided_sha256 != item.sha256:
            raise ValueError("evidence sha256 does not match its content")
        return item

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source": self.source,
            "content": self.content,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class Claim:
    statement: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Claim":
        evidence_ids = raw.get("evidence_ids")
        if not isinstance(evidence_ids, list) or any(not isinstance(item, str) for item in evidence_ids):
            raise ValueError("claim evidence_ids must be a list of strings")
        return cls(_text(raw, "statement"), tuple(evidence_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class Proposal:
    result: Any
    claims: tuple[Claim, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    requested_actions: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Proposal":
        claims_raw = raw.get("claims", [])
        evidence_raw = raw.get("evidence", [])
        actions_raw = raw.get("requested_actions", [])
        if not isinstance(claims_raw, list) or not all(isinstance(item, dict) for item in claims_raw):
            raise ValueError("proposal claims must be a list of objects")
        if not isinstance(evidence_raw, list) or not all(isinstance(item, dict) for item in evidence_raw):
            raise ValueError("proposal evidence must be a list of objects")
        if not isinstance(actions_raw, list) or any(not isinstance(item, str) for item in actions_raw):
            raise ValueError("proposal requested_actions must be a list of strings")
        return cls(
            result=raw.get("result"),
            claims=tuple(Claim.from_dict(item) for item in claims_raw),
            evidence=tuple(EvidenceItem.from_dict(item) for item in evidence_raw),
            requested_actions=tuple(actions_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "claims": [item.to_dict() for item in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
            "requested_actions": list(self.requested_actions),
        }

    def fingerprint(self) -> str:
        raw = self.to_dict()
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifierFeedback:
    gate: str
    code: str
    reason: str
    evidence_ids: tuple[str, ...] = ()
    details: Any | None = None

    def __post_init__(self) -> None:
        for field_name in ("gate", "code", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"feedback {field_name} must be a non-empty string"
                )
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.evidence_ids
        ):
            raise ValueError(
                "feedback evidence_ids must contain non-empty strings"
            )
        object.__setattr__(self, "details", _json_clone(self.details))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VerifierFeedback":
        allowed = {"gate", "code", "reason", "evidence_ids", "details"}
        if set(raw) - allowed:
            raise ValueError(f"unknown feedback fields: {sorted(set(raw) - allowed)}")
        evidence_ids = raw.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            raise ValueError("feedback evidence_ids must be a list")
        return cls(raw.get("gate"), raw.get("code"), raw.get("reason"),
                   tuple(evidence_ids), raw.get("details"))

    def to_dict(self) -> dict[str, Any]:
        result = {
            "gate": self.gate,
            "code": self.code,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.details is not None:
            result["details"] = _json_clone(self.details)
        return result


class AgentRuntime(Protocol):
    def propose(
        self,
        contract: TaskContract,
        feedback: Sequence[VerifierFeedback],
    ) -> Proposal:
        """Return an untrusted proposal. The controller retains authority."""


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _json_clone(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"details must be JSON-serializable: {exc}") from exc
