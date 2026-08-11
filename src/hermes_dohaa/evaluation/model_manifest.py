"""Frozen model identities for preregistered multi-model evaluations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from .protocol import EvaluationProtocol


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ModelManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    slot_id: str
    model_alias: str
    model_artifact_id: str
    provider: str
    backend: str
    backend_version: str
    architecture: Mapping[str, str | int | float | bool]
    context_length: int
    quantization: str
    server_config_sha256: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelArtifact":
        _require_exact_fields(
            raw,
            {
                "slot_id",
                "model_alias",
                "model_artifact_id",
                "provider",
                "backend",
                "backend_version",
                "architecture",
                "context_length",
                "quantization",
                "server_config_sha256",
            },
            "model artifact",
        )
        context_length = raw.get("context_length")
        if (
            isinstance(context_length, bool)
            or not isinstance(context_length, int)
            or not 1 <= context_length <= 10_000_000
        ):
            raise ModelManifestError(
                "context_length must be an integer between 1 and 10000000"
            )
        architecture_raw = raw.get("architecture")
        if not isinstance(architecture_raw, dict) or not architecture_raw:
            raise ModelManifestError("architecture must be a non-empty object")
        architecture: dict[str, str | int | float | bool] = {}
        for key, value in architecture_raw.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(value, (str, int, float, bool))
                or value is None
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                raise ModelManifestError(
                    "architecture must contain named JSON scalar values"
                )
            architecture[key.strip()] = value
        artifact = cls(
            slot_id=_text(raw, "slot_id"),
            model_alias=_text(raw, "model_alias"),
            model_artifact_id=_text(raw, "model_artifact_id"),
            provider=_text(raw, "provider"),
            backend=_text(raw, "backend"),
            backend_version=_text(raw, "backend_version"),
            architecture=MappingProxyType(dict(sorted(architecture.items()))),
            context_length=context_length,
            quantization=_text(raw, "quantization"),
            server_config_sha256=_digest(raw, "server_config_sha256"),
        )
        if any(
            value.lower().startswith("replace-with-")
            for value in (
                artifact.model_alias,
                artifact.model_artifact_id,
                artifact.provider,
                artifact.backend,
                artifact.backend_version,
                artifact.quantization,
            )
        ):
            raise ModelManifestError(
                "model artifact contains an unresolved example placeholder"
            )
        return artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "model_alias": self.model_alias,
            "model_artifact_id": self.model_artifact_id,
            "provider": self.provider,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "architecture": dict(self.architecture),
            "context_length": self.context_length,
            "quantization": self.quantization,
            "server_config_sha256": self.server_config_sha256,
        }


@dataclass(frozen=True, slots=True)
class ModelManifest:
    schema_version: str
    manifest_id: str
    protocol_id: str
    protocol_sha256: str
    frozen_at: str
    models: tuple[ModelArtifact, ...]

    @classmethod
    def create(
        cls,
        protocol: EvaluationProtocol,
        raw_models: list[Mapping[str, Any]],
    ) -> "ModelManifest":
        models = tuple(ModelArtifact.from_dict(item) for item in raw_models)
        manifest = cls(
            schema_version="1.0",
            manifest_id=str(uuid4()),
            protocol_id=protocol.protocol_id,
            protocol_sha256=protocol.sha256(),
            frozen_at=datetime.now(timezone.utc).isoformat(),
            models=models,
        )
        manifest.verify(protocol)
        return manifest

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelManifest":
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "manifest_id",
                "protocol_id",
                "protocol_sha256",
                "frozen_at",
                "models",
            },
            "model manifest",
        )
        if raw.get("schema_version") != "1.0":
            raise ModelManifestError("unsupported model manifest schema")
        manifest_id = _text(raw, "manifest_id")
        try:
            manifest_id = str(UUID(manifest_id))
        except ValueError as exc:
            raise ModelManifestError("manifest_id must be a UUID") from exc
        frozen_at = _timestamp(raw, "frozen_at")
        models_raw = raw.get("models")
        if not isinstance(models_raw, list) or not all(
            isinstance(item, dict) for item in models_raw
        ):
            raise ModelManifestError("models must be a list of objects")
        manifest = cls(
            schema_version="1.0",
            manifest_id=manifest_id,
            protocol_id=_text(raw, "protocol_id"),
            protocol_sha256=_digest(raw, "protocol_sha256"),
            frozen_at=frozen_at,
            models=tuple(ModelArtifact.from_dict(item) for item in models_raw),
        )
        manifest.sha256()
        return manifest

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ModelManifest":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelManifestError(
                f"cannot load model manifest {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise ModelManifestError("model manifest root must be an object")
        return cls.from_dict(raw)

    def verify(self, protocol: EvaluationProtocol) -> None:
        if self.protocol_id != protocol.protocol_id:
            raise ModelManifestError("model manifest protocol_id does not match")
        if self.protocol_sha256 != protocol.sha256():
            raise ModelManifestError(
                "model manifest protocol_sha256 does not match"
            )
        expected_slots = [slot.slot_id for slot in protocol.model_slots]
        actual_slots = [model.slot_id for model in self.models]
        if actual_slots != expected_slots:
            raise ModelManifestError(
                "model manifest slots must exactly match protocol order"
            )
        aliases = [model.model_alias for model in self.models]
        identities = [model.model_artifact_id for model in self.models]
        if len(aliases) != len(set(aliases)):
            raise ModelManifestError("model aliases must be unique")
        if len(identities) != len(set(identities)):
            raise ModelManifestError("model artifact identities must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "protocol_id": self.protocol_id,
            "protocol_sha256": self.protocol_sha256,
            "frozen_at": self.frozen_at,
            "models": [model.to_dict() for model in self.models],
        }

    def sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


def freeze_model_manifest(
    protocol: EvaluationProtocol,
    draft_path: str | Path,
) -> ModelManifest:
    try:
        raw = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelManifestError(
            f"cannot load model manifest draft {draft_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ModelManifestError("model manifest draft root must be an object")
    _require_exact_fields(
        raw,
        {"schema_version", "protocol_id", "protocol_sha256", "models"},
        "model manifest draft",
    )
    if raw.get("schema_version") != "1.0":
        raise ModelManifestError("unsupported model manifest draft schema")
    if raw.get("protocol_id") != protocol.protocol_id:
        raise ModelManifestError("model manifest draft protocol_id does not match")
    if raw.get("protocol_sha256") != protocol.sha256():
        raise ModelManifestError(
            "model manifest draft protocol_sha256 does not match"
        )
    models = raw.get("models")
    if not isinstance(models, list) or not all(
        isinstance(item, dict) for item in models
    ):
        raise ModelManifestError("models must be a list of objects")
    return ModelManifest.create(protocol, models)


def write_model_manifest(path: str | Path, manifest: ModelManifest) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest.to_dict(),
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


def _require_exact_fields(
    raw: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown or missing:
        raise ModelManifestError(
            f"invalid {label} fields; unknown={sorted(unknown)}, "
            f"missing={sorted(missing)}"
        )


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModelManifestError(f"{key} must be a non-empty string")
    return value.strip()


def _digest(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ModelManifestError(f"{key} must be a lowercase SHA-256 digest")
    return value


def _timestamp(raw: Mapping[str, Any], key: str) -> str:
    value = _text(raw, key)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ModelManifestError(f"{key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelManifestError(f"{key} must include a timezone")
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
        raise ModelManifestError(
            f"model manifest is not canonical JSON: {exc}"
        ) from exc
