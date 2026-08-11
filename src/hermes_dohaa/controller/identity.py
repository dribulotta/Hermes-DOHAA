"""Deterministic identity for the controller code and configured gates."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import inspect
import json
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping


_SCHEMA_VERSION = "1.0"
_HEX_DIGITS = frozenset("0123456789abcdef")
_CONTROL_PLANE_MODULES = (
    "hermes_dohaa.contracts.models",
    "hermes_dohaa.runtime.base",
    "hermes_dohaa.assurance.gates",
    "hermes_dohaa.assurance.result_spec",
    "hermes_dohaa.assurance.semantic_assertions",
    "hermes_dohaa.evidence.ledger",
    "hermes_dohaa.controller.identity",
    "hermes_dohaa.controller.semantic_repair",
    "hermes_dohaa.controller.engine",
)


class ControlPlaneIdentityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    name: str
    source_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.name, "component name")
        _require_digest(self.source_sha256, "component source_sha256")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ComponentIdentity":
        _require_exact_fields(raw, {"name", "source_sha256"}, "component")
        return cls(
            name=raw.get("name"),
            source_sha256=raw.get("source_sha256"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class GateIdentity:
    position: int
    gate_type: str
    source_sha256: str
    configuration: Mapping[str, Any]

    def __post_init__(self) -> None:
        if isinstance(self.position, bool) or not isinstance(self.position, int):
            raise ValueError("gate identity position must be an integer")
        if self.position < 0:
            raise ValueError("gate identity position cannot be negative")
        _require_text(self.gate_type, "gate identity gate_type")
        _require_digest(self.source_sha256, "gate identity source_sha256")
        if not isinstance(self.configuration, Mapping):
            raise ValueError("gate identity configuration must be an object")
        cloned = _json_clone(dict(self.configuration))
        object.__setattr__(
            self,
            "configuration",
            MappingProxyType(cloned),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GateIdentity":
        _require_exact_fields(
            raw,
            {"position", "gate_type", "source_sha256", "configuration"},
            "gate identity",
        )
        configuration = raw.get("configuration")
        if not isinstance(configuration, dict):
            raise ValueError("gate identity configuration must be an object")
        return cls(
            position=raw.get("position"),
            gate_type=raw.get("gate_type"),
            source_sha256=raw.get("source_sha256"),
            configuration=_json_clone(configuration),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "gate_type": self.gate_type,
            "source_sha256": self.source_sha256,
            "configuration": _json_clone(dict(self.configuration)),
        }


@dataclass(frozen=True, slots=True)
class ControlPlaneIdentity:
    schema_version: str
    components: tuple[ComponentIdentity, ...]
    gates: tuple[GateIdentity, ...]
    sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f"unsupported control-plane identity schema: {self.schema_version!r}"
            )
        if not self.components:
            raise ValueError("control-plane identity requires components")
        component_names = [item.name for item in self.components]
        if len(component_names) != len(set(component_names)):
            raise ValueError("control-plane component names must be unique")
        if not self.gates:
            raise ValueError("control-plane identity requires gates")
        positions = tuple(item.position for item in self.gates)
        if positions != tuple(range(len(self.gates))):
            raise ValueError("control-plane gate positions must be contiguous")
        _require_digest(self.sha256, "control-plane sha256")
        expected = _identity_digest(
            self.schema_version,
            self.components,
            self.gates,
        )
        if not hmac.compare_digest(self.sha256, expected):
            raise ValueError("control-plane sha256 does not match its manifest")

    @classmethod
    def create(
        cls,
        components: Iterable[ComponentIdentity],
        gates: Iterable[GateIdentity],
    ) -> "ControlPlaneIdentity":
        component_tuple = tuple(components)
        gate_tuple = tuple(gates)
        return cls(
            schema_version=_SCHEMA_VERSION,
            components=component_tuple,
            gates=gate_tuple,
            sha256=_identity_digest(
                _SCHEMA_VERSION,
                component_tuple,
                gate_tuple,
            ),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ControlPlaneIdentity":
        _require_exact_fields(
            raw,
            {"schema_version", "components", "gates", "sha256"},
            "control-plane identity",
        )
        components_raw = raw.get("components")
        gates_raw = raw.get("gates")
        if not isinstance(components_raw, list) or not all(
            isinstance(item, dict) for item in components_raw
        ):
            raise ValueError("control-plane components must be a list of objects")
        if not isinstance(gates_raw, list) or not all(
            isinstance(item, dict) for item in gates_raw
        ):
            raise ValueError("control-plane gates must be a list of objects")
        return cls(
            schema_version=raw.get("schema_version"),
            components=tuple(
                ComponentIdentity.from_dict(item) for item in components_raw
            ),
            gates=tuple(GateIdentity.from_dict(item) for item in gates_raw),
            sha256=raw.get("sha256"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "components": [item.to_dict() for item in self.components],
            "gates": [item.to_dict() for item in self.gates],
            "sha256": self.sha256,
        }


def capture_control_plane_identity(
    configured_gates: Iterable[Any],
) -> ControlPlaneIdentity:
    gates = tuple(configured_gates)
    if not gates:
        raise ControlPlaneIdentityError(
            "at least one gate is required for control-plane identity"
        )

    components = []
    for module_name in _CONTROL_PLANE_MODULES:
        module = importlib.import_module(module_name)
        components.append(
            ComponentIdentity(
                name=module_name,
                source_sha256=_source_sha256(module, module_name),
            )
        )

    gate_identities = []
    for position, gate in enumerate(gates):
        gate_class = type(gate)
        gate_type = f"{gate_class.__module__}.{gate_class.__qualname__}"
        gate_identities.append(
            GateIdentity(
                position=position,
                gate_type=gate_type,
                source_sha256=_source_sha256(gate_class, gate_type),
                configuration=_gate_configuration(gate, gate_type),
            )
        )

    return ControlPlaneIdentity.create(components, gate_identities)


def _gate_configuration(gate: Any, gate_type: str) -> dict[str, Any]:
    if is_dataclass(gate):
        configuration = {
            field.name: getattr(gate, field.name)
            for field in fields(gate)
        }
    elif hasattr(gate, "__dict__"):
        configuration = dict(vars(gate))
    else:
        raise ControlPlaneIdentityError(
            f"gate configuration is not inspectable: {gate_type}"
        )
    try:
        cloned = _json_clone(configuration)
    except (TypeError, ValueError) as exc:
        raise ControlPlaneIdentityError(
            f"gate configuration is not JSON serializable: {gate_type}: {exc}"
        ) from exc
    if not isinstance(cloned, dict):
        raise ControlPlaneIdentityError(
            f"gate configuration is not an object: {gate_type}"
        )
    return cloned


def _source_sha256(subject: Any, name: str) -> str:
    try:
        source = inspect.getsource(subject)
    except (OSError, TypeError) as exc:
        raise ControlPlaneIdentityError(
            f"cannot inspect control-plane source: {name}"
        ) from exc
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _identity_digest(
    schema_version: str,
    components: tuple[ComponentIdentity, ...],
    gates: tuple[GateIdentity, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "components": [item.to_dict() for item in components],
        "gates": [item.to_dict() for item in gates],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


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
        raise ValueError(f"value is not canonical JSON: {exc}") from exc


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _require_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_digest(value: Any, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_DIGITS for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_exact_fields(
    raw: Mapping[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing {label} fields: {sorted(missing)}")
