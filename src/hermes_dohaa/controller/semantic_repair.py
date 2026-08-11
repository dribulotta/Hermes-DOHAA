"""Conservative deterministic repair for contract-visible semantic equalities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from hermes_dohaa.assurance.result_spec import json_equal
from hermes_dohaa.assurance.semantic_assertions import (
    SemanticAssertion,
    SemanticEvaluationError,
    SemanticExpression,
    _evaluate,
    _references_result,
    _resolve_pointer,
    parse_semantic_assertions,
)
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal


@dataclass(frozen=True, slots=True)
class DeterministicSemanticRepair:
    """A value-carrying repair plus value-free audit metadata."""

    proposal: Proposal
    assertion_ids: tuple[str, ...]
    result_pointers: tuple[str, ...]


def propose_deterministic_semantic_repair(
    contract: TaskContract,
    proposal: Proposal,
) -> DeterministicSemanticRepair | None:
    """Build a bounded repair or return ``None`` without changing the proposal.

    A repair is eligible only for equality assertions with one direct result
    reference and one expression that is independent of the result. Existing
    result leaves are replaced in a JSON clone. Any ambiguity, conflict, or
    evaluation error rejects the complete repair candidate.
    """
    raw = contract.inputs.get("semantic_assertions")
    if raw is None:
        return None
    try:
        assertions = parse_semantic_assertions(raw)
        replacements = _collect_replacements(
            contract.inputs,
            proposal.result,
            assertions,
        )
        if not replacements:
            return None
        pointers = tuple(sorted(replacements))
        if _has_overlapping_pointers(pointers):
            return None
        repaired_result = _json_clone(proposal.result)
        for pointer in pointers:
            repaired_result = _replace_existing_pointer(
                repaired_result,
                pointer,
                replacements[pointer][0],
            )
        raw_proposal = _json_clone(proposal.to_dict())
        raw_proposal["result"] = repaired_result
        repaired = Proposal.from_dict(raw_proposal)
    except (SemanticEvaluationError, TypeError, ValueError):
        return None

    assertion_ids = tuple(
        sorted(
            assertion_id
            for _, identifiers in replacements.values()
            for assertion_id in identifiers
        )
    )
    return DeterministicSemanticRepair(
        proposal=repaired,
        assertion_ids=assertion_ids,
        result_pointers=pointers,
    )


def _collect_replacements(
    inputs: Mapping[str, Any],
    result: Any,
    assertions: tuple[SemanticAssertion, ...],
) -> dict[str, tuple[Any, list[str]]]:
    replacements: dict[str, tuple[Any, list[str]]] = {}
    for assertion in assertions:
        target = _repair_target(assertion)
        if target is None:
            continue
        pointer, expression = target
        current = _resolve_pointer(result, pointer, "result")
        expected = _evaluate(expression, inputs, None)
        if json_equal(current, expected):
            continue
        existing = replacements.get(pointer)
        if existing is not None:
            if not json_equal(existing[0], expected):
                raise ValueError("conflicting deterministic replacements")
            existing[1].append(assertion.assertion_id)
            continue
        replacements[pointer] = (expected, [assertion.assertion_id])
    return replacements


def _repair_target(
    assertion: SemanticAssertion,
) -> tuple[str, SemanticExpression] | None:
    if assertion.operator != "equals":
        return None
    left_is_target = _is_direct_result_reference(assertion.left)
    right_is_target = _is_direct_result_reference(assertion.right)
    if left_is_target and not _references_result(assertion.right):
        return assertion.left.pointer or "", assertion.right
    if right_is_target and not _references_result(assertion.left):
        return assertion.right.pointer or "", assertion.left
    return None


def _is_direct_result_reference(expression: SemanticExpression) -> bool:
    return expression.op == "ref" and expression.source == "result"


def _has_overlapping_pointers(pointers: tuple[str, ...]) -> bool:
    for index, pointer in enumerate(pointers):
        for other in pointers[index + 1 :]:
            if not pointer or not other:
                return True
            if other.startswith(pointer + "/") or pointer.startswith(other + "/"):
                return True
    return False


def _replace_existing_pointer(root: Any, pointer: str, value: Any) -> Any:
    replacement = _json_clone(value)
    if not pointer:
        return replacement
    current = root
    tokens = [_decode(token) for token in pointer[1:].split("/")]
    for token in tokens[:-1]:
        current = _child(current, token, pointer)
    final = tokens[-1]
    if isinstance(current, dict):
        if final not in current:
            raise SemanticEvaluationError(
                "reference.missing",
                source="result",
                pointer=pointer,
            )
        current[final] = replacement
        return root
    if isinstance(current, list):
        index = _array_index(final, pointer)
        if index >= len(current):
            raise SemanticEvaluationError(
                "reference.missing",
                source="result",
                pointer=pointer,
            )
        current[index] = replacement
        return root
    raise SemanticEvaluationError(
        "reference.not_container",
        source="result",
        pointer=pointer,
    )


def _child(current: Any, token: str, pointer: str) -> Any:
    if isinstance(current, dict):
        if token not in current:
            raise SemanticEvaluationError(
                "reference.missing",
                source="result",
                pointer=pointer,
            )
        return current[token]
    if isinstance(current, list):
        index = _array_index(token, pointer)
        if index >= len(current):
            raise SemanticEvaluationError(
                "reference.missing",
                source="result",
                pointer=pointer,
            )
        return current[index]
    raise SemanticEvaluationError(
        "reference.not_container",
        source="result",
        pointer=pointer,
    )


def _array_index(token: str, pointer: str) -> int:
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise SemanticEvaluationError(
            "reference.invalid_index",
            source="result",
            pointer=pointer,
        )
    return int(token)


def _decode(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _json_clone(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
