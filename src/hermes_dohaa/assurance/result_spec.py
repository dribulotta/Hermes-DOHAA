"""Parsing and evaluation for contract-visible result specifications."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

JSON_TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}
MAX_DEPTH = 32
MAX_VIOLATIONS = 100


@dataclass(frozen=True, slots=True)
class ResultNode:
    type: str
    enum: tuple[Any, ...] | None = None
    properties: tuple[tuple[str, "ResultNode"], ...] = ()
    required: tuple[str, ...] = ()
    additional_properties: bool = True
    items: "ResultNode | None" = None


def parse_result_spec(raw: Any) -> ResultNode:
    """Parse either the legacy flat format or explicitly versioned v2 format."""
    if not isinstance(raw, Mapping):
        raise ValueError("result_spec must be an object")
    if "spec_version" in raw:
        if raw.get("spec_version") != "2.0":
            raise ValueError("spec_version must be '2.0'")
        node_raw = dict(raw)
        del node_raw["spec_version"]
        return _parse_node(node_raw, 0)
    return _parse_flat(raw)


def validate_result(value: Any, node: ResultNode) -> dict[str, Any] | None:
    violations: list[dict[str, Any]] = []
    total = _validate(value, node, "", violations)
    if not total:
        return None
    return {
        "violation_count": total,
        "reported_violation_count": len(violations),
        "truncated": total > len(violations),
        "violations": violations,
    }


def json_equal(left: Any, right: Any) -> bool:
    """Strict equality for canonical JSON values (including numeric types)."""
    left_type = json_type(left)
    if left_type is None or left_type != json_type(right):
        return False
    if left_type == "object":
        return set(left) == set(right) and all(json_equal(left[k], right[k]) for k in left)
    if left_type == "array":
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def json_type(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number" if math.isfinite(value) else None
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return "object"
    return None


def _parse_flat(raw: Mapping[str, Any]) -> ResultNode:
    allowed = {"required_keys", "additional_keys", "types", "enums"}
    _reject_unknown(raw, allowed)
    required = _string_list(raw.get("required_keys"), "required_keys", nonempty=True)
    additional = raw.get("additional_keys", False)
    if not isinstance(additional, bool):
        raise ValueError("additional_keys must be a boolean")
    types = raw.get("types", {})
    enums = raw.get("enums", {})
    if not isinstance(types, Mapping) or not isinstance(enums, Mapping):
        raise ValueError("types and enums must be objects")
    if (set(types) | set(enums)) - set(required):
        raise ValueError("types and enums may only describe required_keys")
    properties = []
    for key in required:
        kind = types.get(key)
        if kind is None:
            kind = "string" if key not in enums else _infer_enum_type(enums[key])
        if kind not in JSON_TYPES:
            raise ValueError(f"unsupported JSON type {kind!r}")
        enum = enums.get(key)
        parsed_enum = _parse_enum(enum, kind) if enum is not None else None
        properties.append((key, ResultNode(kind, enum=parsed_enum)))
    return ResultNode("object", properties=tuple(properties), required=required,
                      additional_properties=additional)


def _parse_node(raw: Any, depth: int) -> ResultNode:
    if depth > MAX_DEPTH:
        raise ValueError(f"result_spec exceeds maximum depth {MAX_DEPTH}")
    if not isinstance(raw, Mapping):
        raise ValueError("each recursive specification must be an object")
    allowed = {"type", "enum", "properties", "required", "additional_properties", "items"}
    _reject_unknown(raw, allowed)
    kind = raw.get("type")
    if kind not in JSON_TYPES:
        raise ValueError(f"unsupported JSON type {kind!r}")
    object_fields = {"properties", "required", "additional_properties"}
    if kind != "object" and set(raw) & object_fields:
        raise ValueError(f"object fields are incompatible with type {kind!r}")
    if kind != "array" and "items" in raw:
        raise ValueError(f"items is incompatible with type {kind!r}")
    if kind == "array" and "items" not in raw:
        raise ValueError("array specifications require items")
    enum = _parse_enum(raw["enum"], kind) if "enum" in raw else None
    if kind == "object":
        properties = raw.get("properties", {})
        if not isinstance(properties, Mapping) or any(not isinstance(k, str) or not k for k in properties):
            raise ValueError("properties must be an object with non-empty string keys")
        required = _string_list(raw.get("required", []), "required")
        if set(required) - set(properties):
            raise ValueError("required entries must be declared in properties")
        additional = raw.get("additional_properties", True)
        if not isinstance(additional, bool):
            raise ValueError("additional_properties must be a boolean")
        parsed = tuple((key, _parse_node(value, depth + 1)) for key, value in properties.items())
        return ResultNode(kind, enum, parsed, required, additional)
    items = _parse_node(raw["items"], depth + 1) if kind == "array" else None
    return ResultNode(kind, enum, items=items)


def _validate(value: Any, node: ResultNode, path: str, out: list[dict[str, Any]]) -> int:
    actual = json_type(value)
    if actual != node.type:
        _append(out, {"path": path, "code": "result.type_mismatch",
                      "expected_type": node.type, "actual_type": actual or "non_canonical"})
        return 1
    count = 0
    if node.enum is not None and not any(json_equal(value, item) for item in node.enum):
        _append(out, {"path": path, "code": "result.enum_invalid", "allowed_values": list(node.enum)})
        count += 1
    if node.type == "object":
        props = dict(node.properties)
        missing = sorted(set(node.required) - set(value))
        unexpected = sorted(set(value) - set(props)) if not node.additional_properties else []
        if missing or unexpected:
            _append(out, {"path": path, "code": "result.keys_mismatch",
                          "missing_keys": missing, "unexpected_keys": unexpected})
            count += 1
        for key in sorted(set(value) & set(props)):
            count += _validate(value[key], props[key], path + "/" + _escape(key), out)
    elif node.type == "array":
        for index, item in enumerate(value):
            count += _validate(item, node.items, path + f"/{index}", out)
    return count


def _append(out: list[dict[str, Any]], violation: dict[str, Any]) -> None:
    if len(out) < MAX_VIOLATIONS:
        out.append(violation)


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")


def _string_list(value: Any, label: str, nonempty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(v, str) or not v for v in value)
            or len(value) != len(set(value))):
        qualifier = "non-empty " if nonempty else ""
        raise ValueError(f"{label} must be a {qualifier}list of unique strings")
    return tuple(value)


def _parse_enum(value: Any, kind: str) -> tuple[Any, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("enum must be a non-empty array")
    if any(json_type(item) != kind for item in value):
        raise ValueError("enum values must match the declared type")
    if any(json_equal(a, b) for index, a in enumerate(value) for b in value[index + 1:]):
        raise ValueError("enum values must be unique")
    return tuple(value)


def _infer_enum_type(value: Any) -> str:
    if not isinstance(value, list) or not value:
        raise ValueError("enum must be a non-empty array")
    kind = json_type(value[0])
    if kind is None or any(json_type(item) != kind for item in value):
        raise ValueError("enum values must share one JSON type")
    return kind
