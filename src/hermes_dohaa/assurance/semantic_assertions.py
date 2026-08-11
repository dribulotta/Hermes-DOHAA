"""Bounded contract-visible semantic assertions.

The language intentionally has no arbitrary code, implicit oracle access, or
literal expression node. Every compared value must come from visible contract
inputs, the proposal result, or a deterministic operation over those values.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from hermes_dohaa.assurance.result_spec import json_equal, json_type


ASSERTION_OPERATORS = {
    "equals",
    "not_equals",
    "less_than",
    "less_than_or_equal",
    "greater_than",
    "greater_than_or_equal",
    "set_equals",
}
EXPRESSION_OPERATORS = {
    "ref",
    "add",
    "subtract",
    "multiply",
    "divide",
    "abs",
    "round",
    "length",
    "sum",
    "min",
    "max",
    "filter",
    "project",
    "sort_by",
    "at",
    "unique",
    "duration_minutes",
    "add_days",
    "add_business_days",
}
FILTER_OPERATORS = ASSERTION_OPERATORS - {"set_equals"}
MAX_ASSERTIONS = 64
MAX_EXPRESSION_DEPTH = 16
MAX_EXPRESSION_NODES = 512
MAX_EXPRESSION_ARGUMENTS = 64
MAX_COLLECTION_ITEMS = 10_000
MAX_REPORTED_VIOLATIONS = 100
MAX_DATE_OFFSET_DAYS = 3_660
MAX_ASSERTION_ID_LENGTH = 128
MAX_ABSOLUTE_NUMBER = 10**100


@dataclass(frozen=True, slots=True)
class SemanticExpression:
    op: str
    args: tuple["SemanticExpression", ...] = ()
    source: str | None = None
    pointer: str | None = None
    comparator: str | None = None
    order: str | None = None
    index: int | None = None
    digits: int | None = None


@dataclass(frozen=True, slots=True)
class SemanticAssertion:
    assertion_id: str
    operator: str
    left: SemanticExpression
    right: SemanticExpression


class SemanticEvaluationError(RuntimeError):
    """A safe, value-free expression evaluation failure."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = dict(details)

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.code, **self.details}


def parse_semantic_assertions(raw: Any) -> tuple[SemanticAssertion, ...]:
    """Parse and strictly validate the visible assertion list."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("semantic_assertions must be a non-empty array")
    if len(raw) > MAX_ASSERTIONS:
        raise ValueError(
            f"semantic_assertions exceeds maximum count {MAX_ASSERTIONS}"
        )

    counter = [0]
    parsed: list[SemanticAssertion] = []
    identifiers: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each semantic assertion must be an object")
        _require_exact_fields(
            item,
            {"assertion_id", "operator", "left", "right"},
            "semantic assertion",
        )
        assertion_id = item.get("assertion_id")
        if not isinstance(assertion_id, str) or not assertion_id.strip():
            raise ValueError("assertion_id must be a non-empty string")
        assertion_id = assertion_id.strip()
        if len(assertion_id) > MAX_ASSERTION_ID_LENGTH:
            raise ValueError(
                f"assertion_id exceeds maximum length "
                f"{MAX_ASSERTION_ID_LENGTH}"
            )
        if assertion_id in identifiers:
            raise ValueError("semantic assertion IDs must be unique")
        identifiers.add(assertion_id)
        operator = item.get("operator")
        if operator not in ASSERTION_OPERATORS:
            raise ValueError(f"unsupported semantic assertion operator {operator!r}")
        left = _parse_expression(item.get("left"), 0, counter)
        right = _parse_expression(item.get("right"), 0, counter)
        if not (_references_result(left) or _references_result(right)):
            raise ValueError(
                "each semantic assertion must reference the proposal result"
            )
        parsed.append(
            SemanticAssertion(assertion_id, operator, left, right)
        )
    return tuple(parsed)


def validate_semantic_assertions(
    inputs: Mapping[str, Any],
    result: Any,
    assertions: tuple[SemanticAssertion, ...],
) -> dict[str, Any] | None:
    """Return value-free diagnostics for failed or unevaluable assertions."""
    violations: list[dict[str, Any]] = []
    total = 0
    for assertion in assertions:
        try:
            left = _evaluate(assertion.left, inputs, result)
            right = _evaluate(assertion.right, inputs, result)
            passed = _compare(assertion.operator, left, right)
        except SemanticEvaluationError as exc:
            total += 1
            _append(
                violations,
                {
                    "assertion_id": assertion.assertion_id,
                    "code": "semantic.evaluation_error",
                    "operator": assertion.operator,
                    **exc.to_dict(),
                },
            )
            continue
        if not passed:
            total += 1
            _append(
                violations,
                {
                    "assertion_id": assertion.assertion_id,
                    "code": "semantic.assertion_failed",
                    "operator": assertion.operator,
                },
            )
    if not total:
        return None
    return {
        "violation_count": total,
        "reported_violation_count": len(violations),
        "truncated": total > len(violations),
        "violations": violations,
    }


def _parse_expression(
    raw: Any,
    depth: int,
    counter: list[int],
) -> SemanticExpression:
    if depth > MAX_EXPRESSION_DEPTH:
        raise ValueError(
            f"semantic expression exceeds maximum depth {MAX_EXPRESSION_DEPTH}"
        )
    counter[0] += 1
    if counter[0] > MAX_EXPRESSION_NODES:
        raise ValueError(
            f"semantic assertions exceed maximum expression nodes "
            f"{MAX_EXPRESSION_NODES}"
        )
    if not isinstance(raw, Mapping):
        raise ValueError("each semantic expression must be an object")
    op = raw.get("op")
    if op not in EXPRESSION_OPERATORS:
        raise ValueError(f"unsupported semantic expression operator {op!r}")

    if op == "ref":
        _require_exact_fields(raw, {"op", "source", "pointer"}, "ref expression")
        source = raw.get("source")
        if source not in {"inputs", "result"}:
            raise ValueError("ref source must be 'inputs' or 'result'")
        pointer = _validate_pointer(raw.get("pointer"), "ref pointer")
        if source == "inputs" and _is_reserved_input_pointer(pointer):
            raise ValueError("ref cannot address reserved control-plane inputs")
        return SemanticExpression(op, source=source, pointer=pointer)

    allowed = {"op", "args"}
    if op in {"filter", "project", "sort_by"}:
        allowed.add("pointer")
    if op == "filter":
        allowed.add("comparator")
    if op == "sort_by":
        allowed.add("order")
    if op == "at":
        allowed.add("index")
    if op == "round":
        allowed.add("digits")
    _require_exact_fields(
        raw,
        allowed,
        f"{op} expression",
        required={"op", "args"},
    )

    args_raw = raw.get("args")
    if not isinstance(args_raw, list):
        raise ValueError(f"{op} args must be an array")
    minimum, maximum = _argument_bounds(op)
    if not minimum <= len(args_raw) <= maximum:
        if minimum == maximum:
            raise ValueError(f"{op} requires exactly {minimum} arguments")
        raise ValueError(
            f"{op} requires between {minimum} and {maximum} arguments"
        )
    args = tuple(
        _parse_expression(item, depth + 1, counter)
        for item in args_raw
    )
    pointer = None
    comparator = None
    order = None
    index = None
    digits = None
    if op in {"filter", "project", "sort_by"}:
        pointer = _validate_pointer(raw.get("pointer"), f"{op} pointer")
    if op == "filter":
        comparator = raw.get("comparator")
        if comparator not in FILTER_OPERATORS:
            raise ValueError(f"unsupported filter comparator {comparator!r}")
    if op == "sort_by":
        order = raw.get("order", "ascending")
        if order not in {"ascending", "descending"}:
            raise ValueError("sort_by order must be ascending or descending")
    if op == "at":
        index = raw.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("at index must be an integer")
        if not -MAX_COLLECTION_ITEMS <= index < MAX_COLLECTION_ITEMS:
            raise ValueError("at index exceeds the collection bound")
    if op == "round":
        digits = raw.get("digits", 0)
        if isinstance(digits, bool) or not isinstance(digits, int):
            raise ValueError("round digits must be an integer")
        if not -12 <= digits <= 12:
            raise ValueError("round digits must be between -12 and 12")
    return SemanticExpression(
        op,
        args,
        pointer=pointer,
        comparator=comparator,
        order=order,
        index=index,
        digits=digits,
    )


def _argument_bounds(op: str) -> tuple[int, int]:
    if op in {"add", "multiply"}:
        return 2, MAX_EXPRESSION_ARGUMENTS
    if op in {
        "subtract",
        "divide",
        "filter",
        "duration_minutes",
        "add_days",
    }:
        return 2, 2
    if op == "add_business_days":
        return 2, 3
    return 1, 1


def _references_result(expression: SemanticExpression) -> bool:
    return (
        expression.op == "ref"
        and expression.source == "result"
    ) or any(_references_result(item) for item in expression.args)


def _evaluate(
    expression: SemanticExpression,
    inputs: Mapping[str, Any],
    result: Any,
) -> Any:
    op = expression.op
    if op == "ref":
        root = inputs if expression.source == "inputs" else result
        return _resolve_pointer(root, expression.pointer or "", expression.source or "")

    values = [_evaluate(arg, inputs, result) for arg in expression.args]
    if op in {"add", "subtract", "multiply", "divide", "abs", "round"}:
        return _numeric_operation(op, values, expression.digits)
    if op == "length":
        value = values[0]
        if not isinstance(value, (str, list, Mapping)):
            raise _type_error(op, "string, array, or object", value)
        _bounded_collection(value, op)
        return len(value)
    if op in {"sum", "min", "max"}:
        return _aggregate(op, values[0])
    if op == "filter":
        collection = _array(values[0], op)
        expected = values[1]
        selected = []
        for item in collection:
            actual = _resolve_pointer(item, expression.pointer or "", "item")
            if _compare(expression.comparator or "equals", actual, expected):
                selected.append(item)
        return selected
    if op == "project":
        collection = _array(values[0], op)
        return [
            _resolve_pointer(item, expression.pointer or "", "item")
            for item in collection
        ]
    if op == "sort_by":
        collection = _array(values[0], op)
        keyed = [
            (_resolve_pointer(item, expression.pointer or "", "item"), item)
            for item in collection
        ]
        _validate_sort_keys([key for key, _ in keyed])
        return [
            item
            for _, item in sorted(
                keyed,
                key=lambda pair: pair[0],
                reverse=expression.order == "descending",
            )
        ]
    if op == "at":
        collection = _array(values[0], op)
        index = expression.index or 0
        try:
            return collection[index]
        except IndexError as exc:
            raise SemanticEvaluationError(
                "collection.index_out_of_range",
                operation=op,
                index=index,
            ) from exc
    if op == "unique":
        collection = _array(values[0], op)
        seen: set[str] = set()
        result_values = []
        for item in collection:
            key = _strict_key(item)
            if key not in seen:
                seen.add(key)
                result_values.append(item)
        return result_values
    if op == "duration_minutes":
        start = _timestamp(values[0], op)
        end = _timestamp(values[1], op)
        minutes = (end - start).total_seconds() / 60
        return int(minutes) if minutes.is_integer() else minutes
    if op == "add_days":
        start = _date(values[0], op)
        days = _bounded_days(values[1], op)
        return (start + timedelta(days=days)).isoformat()
    if op == "add_business_days":
        start = _date(values[0], op)
        days = _bounded_days(values[1], op)
        holidays = set()
        if len(values) == 3:
            holidays = {_date(item, op) for item in _array(values[2], op)}
        step = 1 if days >= 0 else -1
        remaining = abs(days)
        current = start
        while remaining:
            current += timedelta(days=step)
            if current.weekday() < 5 and current not in holidays:
                remaining -= 1
        return current.isoformat()
    raise SemanticEvaluationError("expression.unsupported", operation=op)


def _numeric_operation(op: str, values: list[Any], digits: int | None) -> Any:
    for value in values:
        _bounded_number(value, op)
    if op == "add":
        result = sum(values)
    elif op == "subtract":
        result = values[0] - values[1]
    elif op == "multiply":
        result = values[0]
        for value in values[1:]:
            result *= value
    elif op == "divide":
        if values[1] == 0:
            raise SemanticEvaluationError("numeric.division_by_zero", operation=op)
        result = values[0] / values[1]
    elif op == "abs":
        result = abs(values[0])
    else:
        result = round(values[0], digits or 0)
    _bounded_number(result, op)
    return result


def _aggregate(op: str, value: Any) -> Any:
    collection = _array(value, op)
    if op in {"min", "max"} and not collection:
        raise SemanticEvaluationError("collection.empty", operation=op)
    for item in collection:
        try:
            _bounded_number(item, op)
        except SemanticEvaluationError as exc:
            if exc.code == "expression.type_mismatch":
                raise _type_error(op, "array of numbers", collection) from exc
            raise
    if op == "sum":
        result = sum(collection)
    elif op == "min":
        result = min(collection)
    else:
        result = max(collection)
    _bounded_number(result, op)
    return result


def _compare(operator: str, left: Any, right: Any) -> bool:
    if operator == "equals":
        return json_equal(left, right)
    if operator == "not_equals":
        return not json_equal(left, right)
    if operator == "set_equals":
        left_array = _array(left, operator)
        right_array = _array(right, operator)
        return {_strict_key(item) for item in left_array} == {
            _strict_key(item) for item in right_array
        }
    if not _comparable(left, right):
        raise SemanticEvaluationError(
            "comparison.type_mismatch",
            operator=operator,
            left_type=json_type(left) or "non_canonical",
            right_type=json_type(right) or "non_canonical",
        )
    if operator == "less_than":
        return left < right
    if operator == "less_than_or_equal":
        return left <= right
    if operator == "greater_than":
        return left > right
    return left >= right


def _resolve_pointer(root: Any, pointer: str, source: str) -> Any:
    current = root
    if not pointer:
        return current
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise SemanticEvaluationError(
                    "reference.missing",
                    source=source,
                    pointer=pointer,
                )
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise SemanticEvaluationError(
                    "reference.invalid_index",
                    source=source,
                    pointer=pointer,
                )
            index = int(token)
            if index >= len(current):
                raise SemanticEvaluationError(
                    "reference.missing",
                    source=source,
                    pointer=pointer,
                )
            current = current[index]
        else:
            raise SemanticEvaluationError(
                "reference.not_container",
                source=source,
                pointer=pointer,
            )
    return current


def _validate_pointer(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a JSON Pointer string")
    if value and not value.startswith("/"):
        raise ValueError(f"{label} must be empty or start with '/'")
    index = 0
    while index < len(value):
        if value[index] == "~":
            if index + 1 >= len(value) or value[index + 1] not in "01":
                raise ValueError(f"{label} contains an invalid escape")
            index += 2
        else:
            index += 1
    return value


def _is_reserved_input_pointer(pointer: str) -> bool:
    if not pointer:
        return True
    return any(
        pointer == prefix or pointer.startswith(prefix + "/")
        for prefix in (
            "/expected_result",
            "/result_spec",
            "/semantic_assertions",
        )
    )


def _array(value: Any, operation: str) -> list[Any]:
    if not isinstance(value, list):
        raise _type_error(operation, "array", value)
    _bounded_collection(value, operation)
    return value


def _bounded_collection(value: Any, operation: str) -> None:
    if len(value) > MAX_COLLECTION_ITEMS:
        raise SemanticEvaluationError(
            "collection.too_large",
            operation=operation,
            maximum=MAX_COLLECTION_ITEMS,
        )


def _validate_sort_keys(values: list[Any]) -> None:
    if not values:
        return
    kinds = {json_type(value) for value in values}
    if kinds <= {"integer", "number"}:
        return
    if len(kinds) != 1 or next(iter(kinds)) != "string":
        raise SemanticEvaluationError(
            "collection.incomparable_sort_keys",
            operation="sort_by",
            key_types=sorted(kind or "non_canonical" for kind in kinds),
        )


def _comparable(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return True
    left_type = json_type(left)
    return left_type == "string" and left_type == json_type(right)


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _bounded_number(value: Any, operation: str) -> None:
    if not _is_number(value):
        raise _type_error(operation, "number", value)
    if abs(value) > MAX_ABSOLUTE_NUMBER:
        raise SemanticEvaluationError(
            "numeric.out_of_range",
            operation=operation,
            maximum_absolute_exponent=100,
        )


def _timestamp(value: Any, operation: str) -> datetime:
    if not isinstance(value, str):
        raise _type_error(operation, "ISO-8601 timestamp", value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SemanticEvaluationError(
            "temporal.invalid_timestamp",
            operation=operation,
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SemanticEvaluationError(
            "temporal.timezone_required",
            operation=operation,
        )
    return parsed


def _date(value: Any, operation: str) -> date:
    if not isinstance(value, str):
        raise _type_error(operation, "ISO-8601 date", value)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SemanticEvaluationError(
            "temporal.invalid_date",
            operation=operation,
        ) from exc


def _bounded_days(value: Any, operation: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(operation, "integer day offset", value)
    if abs(value) > MAX_DATE_OFFSET_DAYS:
        raise SemanticEvaluationError(
            "temporal.offset_too_large",
            operation=operation,
            maximum=MAX_DATE_OFFSET_DAYS,
        )
    return value


def _type_error(operation: str, expected: str, actual: Any) -> SemanticEvaluationError:
    return SemanticEvaluationError(
        "expression.type_mismatch",
        operation=operation,
        expected_type=expected,
        actual_type=json_type(actual) or "non_canonical",
    )


def _strict_key(value: Any) -> str:
    kind = json_type(value)
    if kind is None:
        raise SemanticEvaluationError("value.non_canonical")
    try:
        canonical = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticEvaluationError("value.non_canonical") from exc
    return f"{kind}:{canonical}"


def _append(out: list[dict[str, Any]], violation: dict[str, Any]) -> None:
    if len(out) < MAX_REPORTED_VIOLATIONS:
        out.append(violation)


def _require_exact_fields(
    raw: Mapping[str, Any],
    allowed: set[str],
    label: str,
    *,
    required: set[str] | None = None,
) -> None:
    unknown = set(raw) - allowed
    missing = (allowed if required is None else required) - set(raw)
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing {label} fields: {sorted(missing)}")
