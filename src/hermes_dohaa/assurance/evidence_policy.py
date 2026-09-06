"""Opt-in, contract-owned source commitments and deterministic factual claims.

The contract author owns source selection and truth. This module verifies exact
commitments and specified value relationships, not arbitrary natural language.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from hermes_dohaa.assurance.result_spec import json_equal
from hermes_dohaa.assurance.semantic_assertions import (
    SemanticEvaluationError, _resolve_pointer, _validate_pointer,
)
from hermes_dohaa.runtime.base import Proposal


@dataclass(frozen=True, slots=True)
class SourceCommitment:
    evidence_id: str
    kind: str
    source: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ClaimBinding:
    evidence_id: str
    evidence_pointer: str
    result_pointer: str
    prefix: str
    suffix: str


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    sources: tuple[SourceCommitment, ...]
    claims: tuple[ClaimBinding, ...]


def parse_evidence_policy(raw: Any) -> EvidencePolicy:
    """Reject malformed or empty configurations; absence is handled by the caller."""
    _fields(raw, {"schema_version", "sources", "claims"})
    if raw["schema_version"] != "1.0":
        raise ValueError("unsupported evidence policy version")
    sources = []
    for item in _entries(raw["sources"]):
        _fields(item, {"evidence_id", "kind", "source", "sha256"})
        digest = item["sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("source commitment requires lowercase SHA-256")
        sources.append(SourceCommitment(
            _text(item["evidence_id"], 128), _text(item["kind"], 512),
            _text(item["source"], 512), digest,
        ))
    ids = {source.evidence_id for source in sources}
    if len(ids) != len(sources):
        raise ValueError("source commitment IDs must be unique")
    claims = []
    for item in _entries(raw["claims"]):
        _fields(item, {"evidence_id", "evidence_pointer", "result_pointer", "prefix", "suffix"})
        evidence_id = _text(item["evidence_id"], 128)
        if evidence_id not in ids:
            raise ValueError("claim binding must reference a committed source")
        claims.append(ClaimBinding(
            evidence_id, _pointer(item["evidence_pointer"]), _pointer(item["result_pointer"]),
            _text(item["prefix"], 512, literal=True), _text(item["suffix"], 512, literal=True),
        ))
    if len(set(claims)) != len(claims):
        raise ValueError("claim bindings must be unique")
    return EvidencePolicy(tuple(sources), tuple(claims))


def check_evidence_policy(policy: EvidencePolicy, proposal: Proposal) -> str | None:
    """Return a value-free failure code, or None when every binding passes."""
    evidence = {item.evidence_id: item for item in proposal.evidence}
    if len(evidence) != len(proposal.evidence) or set(evidence) != {
        source.evidence_id for source in policy.sources
    }:
        return "evidence.binding_mismatch"
    for source in policy.sources:
        item = evidence[source.evidence_id]
        try:
            digest = hashlib.sha256(_canonical(item.content).encode("utf-8")).hexdigest()
        except (TypeError, ValueError, RecursionError):
            return "evidence.binding_mismatch"
        if (item.kind != source.kind or item.source != source.source
                or item.sha256 != source.sha256 or digest != source.sha256):
            return "evidence.binding_mismatch"

    expected = []
    try:
        for binding in policy.claims:
            value = _resolve_pointer(evidence[binding.evidence_id].content,
                                     binding.evidence_pointer, "evidence")
            actual = _resolve_pointer(proposal.result, binding.result_pointer, "result")
            if not json_equal(value, actual):
                return "evidence.claim_binding_mismatch"
            statement = binding.prefix + _canonical(value) + binding.suffix
            expected.append((statement, (binding.evidence_id,)))
        if len(set(expected)) != len(expected):
            return "evidence.claim_binding_mismatch"
        observed = [(claim.statement, claim.evidence_ids) for claim in proposal.claims]
        if Counter(observed) != Counter(expected):
            return "evidence.claim_binding_mismatch"
    except (SemanticEvaluationError, TypeError, ValueError, RecursionError):
        return "evidence.claim_binding_mismatch"
    return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _fields(raw: Any, expected: set[str]) -> None:
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("evidence policy object has missing or unknown fields")


def _entries(raw: Any) -> list[Any]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise ValueError("evidence policy lists require 1 to 32 entries")
    return raw


def _text(raw: Any, limit: int, *, literal: bool = False) -> str:
    if (not isinstance(raw, str) or len(raw) > limit
            or (not literal and (not raw.strip() or raw != raw.strip()))):
        raise ValueError("evidence policy text is invalid or exceeds its limit")
    raw.encode("utf-8")
    return raw


def _pointer(raw: Any) -> str:
    value = _text(raw, 1024, literal=True)
    return _validate_pointer(value, "evidence policy pointer")
