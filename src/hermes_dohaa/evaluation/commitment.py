"""Immutable commitments for protected evaluation suites."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from .models import EvaluationSuite


_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40,64}")


class SuiteCommitmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SuiteCommitment:
    schema_version: str
    commitment_id: str
    visibility: str
    suite_id: str
    suite_sha256: str
    case_count: int
    domain_counts: Mapping[str, int]
    protocol_commit: str
    frozen_at: str

    @classmethod
    def create(
        cls,
        suite: EvaluationSuite,
        *,
        protocol_commit: str,
    ) -> "SuiteCommitment":
        case_count = len(suite.cases)
        domain_counts: dict[str, int] = {}
        for case in suite.cases:
            domain_counts[case.domain] = domain_counts.get(case.domain, 0) + 1
        _validate_pilot_shape(case_count, domain_counts)
        normalized_commit = protocol_commit.strip().lower()
        if _COMMIT_PATTERN.fullmatch(normalized_commit) is None:
            raise SuiteCommitmentError(
                "protocol_commit must be a 40- to 64-character lowercase hex digest"
            )
        return cls(
            schema_version="1.0",
            commitment_id=str(uuid4()),
            visibility="protected_holdout",
            suite_id=suite.suite_id,
            suite_sha256=suite.sha256(),
            case_count=case_count,
            domain_counts=MappingProxyType(dict(sorted(domain_counts.items()))),
            protocol_commit=normalized_commit,
            frozen_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SuiteCommitment":
        allowed = {
            "schema_version",
            "commitment_id",
            "visibility",
            "suite_id",
            "suite_sha256",
            "case_count",
            "domain_counts",
            "protocol_commit",
            "frozen_at",
        }
        unknown = set(raw) - allowed
        missing = allowed - set(raw)
        if unknown or missing:
            raise SuiteCommitmentError(
                "invalid suite commitment fields; "
                f"unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        if raw.get("schema_version") != "1.0":
            raise SuiteCommitmentError("unsupported suite commitment schema")
        if raw.get("visibility") != "protected_holdout":
            raise SuiteCommitmentError(
                "suite commitment visibility must be protected_holdout"
            )
        domain_counts_raw = raw.get("domain_counts")
        if not isinstance(domain_counts_raw, dict) or not domain_counts_raw:
            raise SuiteCommitmentError("domain_counts must be a non-empty object")
        domain_counts: dict[str, int] = {}
        for domain, count in domain_counts_raw.items():
            if (
                not isinstance(domain, str)
                or not domain.strip()
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
            ):
                raise SuiteCommitmentError("domain_counts contains an invalid entry")
            domain_counts[domain] = count
        case_count = raw.get("case_count")
        if (
            isinstance(case_count, bool)
            or not isinstance(case_count, int)
            or case_count != sum(domain_counts.values())
        ):
            raise SuiteCommitmentError(
                "case_count must equal the sum of domain_counts"
            )
        _validate_pilot_shape(case_count, domain_counts)
        commitment_id = _text(raw, "commitment_id")
        try:
            normalized_commitment_id = str(UUID(commitment_id))
        except ValueError as exc:
            raise SuiteCommitmentError(
                "commitment_id must be a UUID"
            ) from exc
        frozen_at = _text(raw, "frozen_at")
        try:
            frozen_datetime = datetime.fromisoformat(frozen_at)
        except ValueError as exc:
            raise SuiteCommitmentError(
                "frozen_at must be an ISO-8601 timestamp"
            ) from exc
        if frozen_datetime.tzinfo is None:
            raise SuiteCommitmentError("frozen_at must include a timezone")
        commitment = cls(
            schema_version="1.0",
            commitment_id=normalized_commitment_id,
            visibility="protected_holdout",
            suite_id=_text(raw, "suite_id"),
            suite_sha256=_digest(raw, "suite_sha256", 64),
            case_count=case_count,
            domain_counts=MappingProxyType(dict(sorted(domain_counts.items()))),
            protocol_commit=_digest_range(raw, "protocol_commit", 40, 64),
            frozen_at=frozen_at,
        )
        commitment.sha256()
        return commitment

    @classmethod
    def from_json_file(cls, path: str | Path) -> "SuiteCommitment":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SuiteCommitmentError(
                f"cannot load suite commitment {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise SuiteCommitmentError("suite commitment root must be an object")
        return cls.from_dict(raw)

    def verify(self, suite: EvaluationSuite) -> None:
        actual_domains: dict[str, int] = {}
        for case in suite.cases:
            actual_domains[case.domain] = actual_domains.get(case.domain, 0) + 1
        checks = {
            "suite_id": (suite.suite_id, self.suite_id),
            "suite_sha256": (suite.sha256(), self.suite_sha256),
            "case_count": (len(suite.cases), self.case_count),
            "domain_counts": (
                dict(sorted(actual_domains.items())),
                dict(self.domain_counts),
            ),
        }
        mismatches = [
            name for name, (actual, expected) in checks.items() if actual != expected
        ]
        if mismatches:
            raise SuiteCommitmentError(
                "suite does not match its frozen commitment: "
                f"{', '.join(mismatches)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "commitment_id": self.commitment_id,
            "visibility": self.visibility,
            "suite_id": self.suite_id,
            "suite_sha256": self.suite_sha256,
            "case_count": self.case_count,
            "domain_counts": dict(self.domain_counts),
            "protocol_commit": self.protocol_commit,
            "frozen_at": self.frozen_at,
        }

    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


def write_suite_commitment(
    path: str | Path,
    commitment: SuiteCommitment,
) -> None:
    _write_private_json(path, commitment.to_dict())


def _write_private_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
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
        raise SuiteCommitmentError(
            f"value is not canonical JSON: {exc}"
        ) from exc


def _validate_pilot_shape(
    case_count: int,
    domain_counts: Mapping[str, int],
) -> None:
    if not 30 <= case_count <= 50:
        raise SuiteCommitmentError(
            "protected pilot suites must contain between 30 and 50 cases"
        )
    if len(domain_counts) < 3:
        raise SuiteCommitmentError(
            "protected pilot suites must contain at least three domains"
        )
    sparse = sorted(
        domain for domain, count in domain_counts.items() if count < 5
    )
    if sparse:
        raise SuiteCommitmentError(
            "each protected pilot domain must contain at least five cases; "
            f"too small: {sparse}"
        )


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SuiteCommitmentError(f"{key} must be a non-empty string")
    return value.strip()


def _digest(raw: Mapping[str, Any], key: str, length: int) -> str:
    return _digest_range(raw, key, length, length)


def _digest_range(
    raw: Mapping[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> str:
    value = _text(raw, key).lower()
    if not minimum <= len(value) <= maximum or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise SuiteCommitmentError(f"{key} must be a lowercase hex digest")
    return value
