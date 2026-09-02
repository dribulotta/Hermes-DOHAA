"""Value-free scopes and monotonic selection for rule-aware repair."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from hermes_dohaa.assurance.gates import Gate, GateResult
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback


_POLICY_FIELDS = {
    "schema_version",
    "mode",
    "preserve_unlisted",
    "require_strict_improvement",
    "immutable_paths",
}
_SCOPE_FIELDS = {
    "schema_version",
    "failed_rule_ids",
    "rule_ids",
    "editable_paths",
    "atomic_groups",
    "source_pointers",
}
_PROPOSAL_ROOTS = (
    "/result",
    "/claims",
    "/evidence",
    "/requested_actions",
)
_RESERVED_INPUT_ROOTS = (
    "/expected_result",
    "/result_spec",
    "/semantic_assertions",
    "/repair_policy",
)
_MAX_ITEMS = 512
_MAX_POLICY_PATHS = 256
_MAX_TEXT_LENGTH = 256
_MAX_POINTER_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class AtomicRepairGroup:
    group_id: str
    editable_paths: tuple[str, ...]

    @classmethod
    def from_raw(cls, raw: Any) -> "AtomicRepairGroup":
        if not isinstance(raw, Mapping):
            raise ValueError("repair atomic group must be an object")
        _require_exact_fields(
            raw,
            {"group_id", "editable_paths"},
            "repair atomic group",
        )
        group_id = _text(raw.get("group_id"), "repair group_id")
        paths = _proposal_paths(
            raw.get("editable_paths"),
            "repair atomic group editable_paths",
        )
        return cls(group_id, paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "editable_paths": list(self.editable_paths),
        }


@dataclass(frozen=True, slots=True)
class RepairSourcePointer:
    source: str
    pointer: str

    @classmethod
    def from_raw(cls, raw: Any) -> "RepairSourcePointer":
        if not isinstance(raw, Mapping):
            raise ValueError("repair source pointer must be an object")
        _require_exact_fields(
            raw,
            {"source", "pointer"},
            "repair source pointer",
        )
        if raw.get("source") != "contract.inputs":
            raise ValueError("repair source must be contract.inputs")
        pointer = _json_pointer(
            raw.get("pointer"),
            "repair source pointer",
            allow_root=False,
        )
        if _overlaps_any(pointer, _RESERVED_INPUT_ROOTS):
            raise ValueError("repair source pointer addresses reserved input")
        return cls("contract.inputs", pointer)

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "pointer": self.pointer}


@dataclass(frozen=True, slots=True)
class RepairScope:
    failed_rule_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    editable_paths: tuple[str, ...]
    atomic_groups: tuple[AtomicRepairGroup, ...]
    source_pointers: tuple[RepairSourcePointer, ...]
    schema_version: str = "1.0"

    @classmethod
    def from_raw(cls, raw: Any) -> "RepairScope":
        if not isinstance(raw, Mapping):
            raise ValueError("repair_scope must be an object")
        _require_exact_fields(raw, _SCOPE_FIELDS, "repair_scope")
        if raw.get("schema_version") != "1.0":
            raise ValueError("unsupported repair_scope schema_version")

        failed = _text_tuple(raw.get("failed_rule_ids"), "failed_rule_ids")
        rules = _text_tuple(raw.get("rule_ids"), "rule_ids")
        if not failed:
            raise ValueError("repair_scope failed_rule_ids cannot be empty")
        if not set(failed) <= set(rules):
            raise ValueError("failed_rule_ids must be a subset of rule_ids")
        paths = _proposal_paths(raw.get("editable_paths"), "editable_paths")

        groups_raw = raw.get("atomic_groups")
        if not isinstance(groups_raw, list) or len(groups_raw) > _MAX_ITEMS:
            raise ValueError("atomic_groups must be a bounded array")
        groups = tuple(AtomicRepairGroup.from_raw(item) for item in groups_raw)
        group_ids = [item.group_id for item in groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("repair atomic group IDs must be unique")
        grouped_paths: set[str] = set()
        for group in groups:
            if not set(group.editable_paths) <= set(paths):
                raise ValueError("atomic group paths must be editable")
            overlap = grouped_paths & set(group.editable_paths)
            if overlap:
                raise ValueError("editable paths cannot belong to multiple groups")
            grouped_paths.update(group.editable_paths)

        sources_raw = raw.get("source_pointers")
        if not isinstance(sources_raw, list) or len(sources_raw) > _MAX_ITEMS:
            raise ValueError("source_pointers must be a bounded array")
        sources = tuple(
            sorted(
                {RepairSourcePointer.from_raw(item) for item in sources_raw},
                key=lambda item: (item.source, item.pointer),
            )
        )
        return cls(
            failed_rule_ids=failed,
            rule_ids=rules,
            editable_paths=paths,
            atomic_groups=tuple(sorted(groups, key=lambda item: item.group_id)),
            source_pointers=sources,
        )

    @classmethod
    def merge(cls, scopes: Sequence["RepairScope"]) -> "RepairScope":
        if not scopes:
            raise ValueError("at least one repair scope is required")
        groups: dict[str, set[str]] = {}
        for scope in scopes:
            for group in scope.atomic_groups:
                groups.setdefault(group.group_id, set()).update(
                    group.editable_paths
                )
        return cls.from_raw(
            {
                "schema_version": "1.0",
                "failed_rule_ids": sorted(
                    {item for scope in scopes for item in scope.failed_rule_ids}
                ),
                "rule_ids": sorted(
                    {item for scope in scopes for item in scope.rule_ids}
                ),
                "editable_paths": sorted(
                    {item for scope in scopes for item in scope.editable_paths}
                ),
                "atomic_groups": [
                    {
                        "group_id": group_id,
                        "editable_paths": sorted(paths),
                    }
                    for group_id, paths in sorted(groups.items())
                ],
                "source_pointers": [
                    item.to_dict()
                    for item in sorted(
                        {
                            item
                            for scope in scopes
                            for item in scope.source_pointers
                        },
                        key=lambda item: (item.source, item.pointer),
                    )
                ],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "failed_rule_ids": list(self.failed_rule_ids),
            "rule_ids": list(self.rule_ids),
            "editable_paths": list(self.editable_paths),
            "atomic_groups": [item.to_dict() for item in self.atomic_groups],
            "source_pointers": [item.to_dict() for item in self.source_pointers],
        }


@dataclass(frozen=True, slots=True)
class RuleAwareRepairPolicy:
    immutable_paths: tuple[str, ...] = ()
    schema_version: str = "1.0"
    mode: str = "rule_aware"
    preserve_unlisted: bool = True
    require_strict_improvement: bool = True

    @classmethod
    def from_raw(cls, raw: Any) -> "RuleAwareRepairPolicy":
        if not isinstance(raw, Mapping):
            raise ValueError("repair_policy must be an object")
        unknown = set(raw) - _POLICY_FIELDS
        missing = {"schema_version", "mode"} - set(raw)
        if unknown:
            raise ValueError(f"unknown repair_policy fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"missing repair_policy fields: {sorted(missing)}")
        if raw.get("schema_version") != "1.0":
            raise ValueError("unsupported repair_policy schema_version")
        if raw.get("mode") != "rule_aware":
            raise ValueError("repair_policy mode must be rule_aware")
        preserve = raw.get("preserve_unlisted", True)
        strict = raw.get("require_strict_improvement", True)
        if preserve is not True:
            raise ValueError("repair_policy preserve_unlisted must be true")
        if strict is not True:
            raise ValueError(
                "repair_policy require_strict_improvement must be true"
            )
        immutable = _proposal_paths(
            raw.get("immutable_paths", []),
            "immutable_paths",
            allow_empty=True,
            maximum_items=_MAX_POLICY_PATHS,
        )
        return cls(immutable_paths=immutable)

    @classmethod
    def from_contract(
        cls,
        contract: TaskContract,
    ) -> "RuleAwareRepairPolicy | None":
        if "repair_policy" not in contract.inputs:
            return None
        return cls.from_raw(contract.inputs["repair_policy"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "preserve_unlisted": self.preserve_unlisted,
            "require_strict_improvement": self.require_strict_improvement,
            "immutable_paths": list(self.immutable_paths),
        }


@dataclass(frozen=True, slots=True)
class CandidateChangeAssessment:
    allowed: bool
    reason_code: str
    changed_paths: tuple[str, ...]
    outside_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "changed_paths": list(self.changed_paths),
            "outside_paths": list(self.outside_paths),
        }


@dataclass(frozen=True, slots=True)
class FailureComparison:
    accepted: bool
    reason_code: str
    resolved: tuple[tuple[str, str], ...]
    introduced: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "resolved_failures": [
                {"gate": gate, "rule_id": rule_id}
                for gate, rule_id in self.resolved
            ],
            "introduced_failures": [
                {"gate": gate, "rule_id": rule_id}
                for gate, rule_id in self.introduced
            ],
        }


def derive_repair_scope(
    gates: Sequence[Gate],
    results: Sequence[GateResult],
) -> RepairScope | None:
    """Select one isolated repair unit, ignoring explicit oracle-only gates.

    Every non-oracle failure must advertise a valid scope.  Disjoint scopes are
    handled on later attempts; overlapping scopes are closed together so a
    candidate cannot change a field shared by an untargeted failing gate.
    """
    if len(gates) != len(results):
        raise ValueError("gate and result counts differ")
    scopes: list[RepairScope] = []
    seen_failed_rule_ids: set[str] = set()
    for gate, result in zip(gates, results, strict=True):
        if result.passed:
            continue
        feedback_mode = getattr(gate, "repair_feedback_mode", None)
        if feedback_mode == "oracle_only":
            continue
        if feedback_mode != "contract_visible_scope":
            return None
        details = result.details
        if not isinstance(details, Mapping) or "repair_scope" not in details:
            return None
        try:
            scope = RepairScope.from_raw(details["repair_scope"])
        except ValueError:
            return None
        if seen_failed_rule_ids & set(scope.failed_rule_ids):
            return None
        seen_failed_rule_ids.update(scope.failed_rule_ids)
        scopes.append(scope)
    if not scopes:
        return None

    selected = [scopes[0]]
    remaining = list(scopes[1:])
    changed = True
    while changed:
        changed = False
        selected_paths = tuple(
            path
            for scope in selected
            for path in scope.editable_paths
        )
        selected_group_ids = {
            group.group_id
            for scope in selected
            for group in scope.atomic_groups
        }
        for scope in tuple(remaining):
            overlaps_path = any(
                _overlaps_any(path, selected_paths)
                for path in scope.editable_paths
            )
            overlaps_group = bool(
                selected_group_ids
                & {group.group_id for group in scope.atomic_groups}
            )
            if overlaps_path or overlaps_group:
                selected.append(scope)
                remaining.remove(scope)
                changed = True
    try:
        return RepairScope.merge(selected)
    except ValueError:
        return None


def make_scoped_feedback(scope: RepairScope) -> tuple[VerifierFeedback, ...]:
    return (
        VerifierFeedback(
            gate="repair_policy",
            code="repair.scoped_retry",
            reason=(
                "Revise only the verifier-authorized scope and preserve every "
                "unlisted proposal field exactly."
            ),
            details={"repair_scope": scope.to_dict()},
        ),
    )


def assess_candidate_changes(
    baseline: Proposal,
    candidate: Proposal,
    scope: RepairScope,
    policy: RuleAwareRepairPolicy,
) -> CandidateChangeAssessment:
    changed = tuple(sorted(_changed_paths(baseline.to_dict(), candidate.to_dict())))
    outside = tuple(
        path
        for path in changed
        if not _covered_by(path, scope.editable_paths)
        or _overlaps_any(path, policy.immutable_paths)
    )
    return CandidateChangeAssessment(
        allowed=not outside,
        reason_code=(
            "repair.scope_valid"
            if not outside
            else "repair.change_out_of_scope"
        ),
        changed_paths=changed,
        outside_paths=outside,
    )


def compare_failure_sets(
    before: Iterable[GateResult],
    after: Iterable[GateResult],
    *,
    gates: Sequence[Gate] | None = None,
    required_resolved_rule_ids: Iterable[str] = (),
) -> FailureComparison:
    before_results = tuple(before)
    after_results = tuple(after)
    if gates is not None and (
        len(gates) != len(before_results)
        or len(gates) != len(after_results)
    ):
        raise ValueError("gate and result counts differ")
    before_set = _failure_atoms(before_results, gates=gates)
    after_set = _failure_atoms(after_results, gates=gates)
    resolved = tuple(sorted(before_set - after_set))
    introduced = tuple(sorted(after_set - before_set))
    if introduced:
        return FailureComparison(
            False,
            "repair.failure_regression",
            resolved,
            introduced,
        )
    required_ids = set(required_resolved_rule_ids)
    required_before = {
        atom for atom in before_set if atom[1] in required_ids
    }
    if required_ids and (
        not required_before or required_before & after_set
    ):
        return FailureComparison(
            False,
            "repair.target_not_resolved",
            resolved,
            (),
        )
    if not resolved:
        return FailureComparison(
            False,
            "repair.no_strict_improvement",
            (),
            (),
        )
    return FailureComparison(
        True,
        "repair.strict_improvement",
        resolved,
        (),
    )


def _failure_atoms(
    results: Iterable[GateResult],
    *,
    gates: Sequence[Gate] | None = None,
) -> set[tuple[str, str]]:
    atoms: set[tuple[str, str]] = set()
    results_tuple = tuple(results)
    if gates is not None and len(gates) != len(results_tuple):
        raise ValueError("gate and result counts differ")
    for index, result in enumerate(results_tuple):
        if (
            gates is not None
            and getattr(gates[index], "repair_feedback_mode", None)
            == "oracle_only"
        ):
            continue
        if result.passed:
            continue
        details = result.details
        raw_violations = (
            details.get("violations")
            if isinstance(details, Mapping)
            else None
        )
        if isinstance(raw_violations, list):
            violation_ids = {
                item.get("assertion_id")
                for item in raw_violations
                if isinstance(item, Mapping)
                and isinstance(item.get("assertion_id"), str)
                and item.get("assertion_id").strip()
            }
            if violation_ids:
                atoms.update(
                    (result.gate, rule_id)
                    for rule_id in violation_ids
                )
                continue
        raw_scope = (
            details.get("repair_scope")
            if isinstance(details, Mapping)
            else None
        )
        if raw_scope is not None:
            try:
                scope = RepairScope.from_raw(raw_scope)
            except ValueError:
                scope = None
            if scope is not None:
                atoms.update(
                    (result.gate, rule_id)
                    for rule_id in scope.failed_rule_ids
                )
                continue
        atoms.add((result.gate, result.failure_code or "gate.failed"))
    return atoms


def _changed_paths(before: Any, after: Any, path: str = "") -> set[str]:
    if _strict_equal(before, after):
        return set()
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: set[str] = set()
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{_encode(str(key))}"
            if key not in before or key not in after:
                paths.add(child)
            else:
                paths.update(_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return {path}
        paths: set[str] = set()
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            paths.update(_changed_paths(left, right, f"{path}/{index}"))
        return paths
    return {path}


def _strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return (
            set(left) == set(right)
            and all(_strict_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)):
        return (
            len(left) == len(right)
            and all(
                _strict_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
        )
    if isinstance(left, float):
        return (
            math.isfinite(left)
            and math.isfinite(right)
            and left == right
            and (
                left != 0.0
                or math.copysign(1.0, left) == math.copysign(1.0, right)
            )
        )
    return left == right


def _covered_by(path: str, allowed: tuple[str, ...]) -> bool:
    return any(
        path == candidate or path.startswith(candidate + "/")
        for candidate in allowed
    )


def _overlaps_any(path: str, candidates: Iterable[str]) -> bool:
    return any(
        path == candidate
        or path.startswith(candidate + "/")
        or candidate.startswith(path + "/")
        for candidate in candidates
    )


def _proposal_paths(
    raw: Any,
    label: str,
    *,
    allow_empty: bool = False,
    maximum_items: int = _MAX_ITEMS,
) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > maximum_items:
        raise ValueError(f"{label} must be a bounded array")
    parsed = tuple(
        _json_pointer(item, label, allow_root=False)
        for item in raw
    )
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{label} must contain unique pointers")
    paths = tuple(sorted(parsed))
    if not paths and not allow_empty:
        raise ValueError(f"{label} cannot be empty")
    if any(
        not any(
            path == root or path.startswith(root + "/")
            for root in _PROPOSAL_ROOTS
        )
        for path in paths
    ):
        raise ValueError(f"{label} must address proposal fields")
    if _has_overlap(paths):
        raise ValueError(f"{label} contains overlapping pointers")
    return paths


def _text_tuple(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
        raise ValueError(f"{label} must be a bounded array")
    values = tuple(sorted({_text(item, label) for item in raw}))
    if len(values) != len(raw):
        raise ValueError(f"{label} must contain unique strings")
    return values


def _text(raw: Any, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = raw.strip()
    if len(value) > _MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is too long")
    return value


def _json_pointer(raw: Any, label: str, *, allow_root: bool) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{label} must contain JSON Pointers")
    if len(raw) > _MAX_POINTER_LENGTH:
        raise ValueError(f"{label} pointer is too long")
    if not raw:
        if allow_root:
            return raw
        raise ValueError(f"{label} cannot contain the root pointer")
    if not raw.startswith("/"):
        raise ValueError(f"{label} pointer must start with '/'")
    index = 0
    while index < len(raw):
        if raw[index] == "~":
            if index + 1 >= len(raw) or raw[index + 1] not in "01":
                raise ValueError(f"{label} pointer contains an invalid escape")
            index += 2
        else:
            index += 1
    return raw


def _has_overlap(paths: tuple[str, ...]) -> bool:
    return any(
        left != right
        and (
            left.startswith(right + "/")
            or right.startswith(left + "/")
        )
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    )


def _encode(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _require_exact_fields(
    raw: Mapping[str, Any],
    fields: set[str],
    label: str,
) -> None:
    unknown = set(raw) - fields
    missing = fields - set(raw)
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing {label} fields: {sorted(missing)}")
