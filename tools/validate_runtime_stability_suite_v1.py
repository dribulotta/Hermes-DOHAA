#!/usr/bin/env python3
"""Validate the public runtime-stability suite without contacting a model."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hermes_dohaa.assurance.gates import (  # noqa: E402
    ResultSpecGate,
    SemanticAssertionsGate,
)
from hermes_dohaa.evaluation.models import EvaluationSuite  # noqa: E402
from hermes_dohaa.runtime.base import Proposal  # noqa: E402


DEFAULT_SUITE = REPO_ROOT / "examples/runtime-stability-suite-v1.json"
DEFAULT_MANIFEST = REPO_ROOT / "examples/runtime-stability-suite-v1.manifest.json"
DEFAULT_PUBLIC_REFERENCE = REPO_ROOT / "examples/evaluation-suite.json"
SUITE_ID = "public-runtime-stability-v1"
DOMAINS = (
    "evidence_synthesis",
    "quantitative_reconciliation",
    "structured_extraction",
    "temporal_reasoning",
)
EXPECTED_MANIFEST_FIELDS = {
    "schema_version",
    "diagnostic_id",
    "suite_id",
    "suite_canonical_sha256",
    "case_count",
    "domains",
    "domain_counts",
    "case_order",
    "timeout_seconds",
    "smoke_repetitions",
    "soak_repetitions",
    "expected_requests_per_model",
    "scope",
}
MIN_CONTRACT_BYTES = 2400
MAX_CONTRACT_BYTES = 4500
MAX_OBJECTIVE_TOKEN_JACCARD = 0.60


class RuntimeStabilitySuiteError(ValueError):
    pass


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeStabilitySuiteError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeStabilitySuiteError(f"{label} root must be an object")
    return raw


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _jaccard(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def validate_runtime_stability_suite(
    suite_path: Path = DEFAULT_SUITE,
    manifest_path: Path = DEFAULT_MANIFEST,
    public_reference_path: Path = DEFAULT_PUBLIC_REFERENCE,
) -> dict[str, Any]:
    suite_raw = _load_object(suite_path, "suite")
    manifest = _load_object(manifest_path, "manifest")
    public_reference = EvaluationSuite.from_json_file(public_reference_path)
    suite = EvaluationSuite.from_dict(suite_raw)

    if suite.suite_id != SUITE_ID:
        raise RuntimeStabilitySuiteError(f"unexpected suite_id: {suite.suite_id}")
    if len(suite.cases) != 16:
        raise RuntimeStabilitySuiteError("runtime-stability suite must contain 16 cases")
    expected_domains = [domain for _ in range(4) for domain in DOMAINS]
    actual_domains = [case.domain for case in suite.cases]
    if actual_domains != expected_domains:
        raise RuntimeStabilitySuiteError("cases must use the interleaved E-Q-X-R order")
    counts = Counter(actual_domains)
    if counts != Counter({domain: 4 for domain in DOMAINS}):
        raise RuntimeStabilitySuiteError("each diagnostic domain must contain four cases")

    if set(manifest) != EXPECTED_MANIFEST_FIELDS:
        raise RuntimeStabilitySuiteError("manifest fields do not match the fixed diagnostic schema")
    expected_manifest = {
        "schema_version": "1.0",
        "diagnostic_id": SUITE_ID,
        "suite_id": SUITE_ID,
        "suite_canonical_sha256": suite.sha256(),
        "case_count": 16,
        "domains": list(DOMAINS),
        "domain_counts": {domain: 4 for domain in DOMAINS},
        "case_order": [case.case_id for case in suite.cases],
        "timeout_seconds": 300,
        "smoke_repetitions": 1,
        "soak_repetitions": 3,
        "expected_requests_per_model": {
            "smoke_if_dohaa_uses_one_call": 64,
            "smoke_maximum": 80,
            "soak_if_dohaa_uses_one_call": 192,
            "soak_maximum": 240,
        },
        "scope": "runtime stability only; public development evidence",
    }
    if manifest != expected_manifest:
        raise RuntimeStabilitySuiteError("manifest does not match the suite or fixed execution envelope")

    contract_sizes: list[int] = []
    assertion_count = 0
    for case, case_raw in zip(suite.cases, suite_raw["cases"], strict=True):
        if "expected_result" in case.contract.inputs:
            raise RuntimeStabilitySuiteError(f"oracle leaked into inputs for {case.case_id}")
        assertions = case.contract.inputs.get("semantic_assertions")
        if not isinstance(assertions, list) or not assertions:
            raise RuntimeStabilitySuiteError(f"missing semantic assertions for {case.case_id}")
        assertion_count += len(assertions)
        proposal = Proposal(case.expected_result)
        result_spec = ResultSpecGate().evaluate(case.contract, proposal)
        semantics = SemanticAssertionsGate().evaluate(case.contract, proposal)
        if not result_spec.passed or not semantics.passed:
            raise RuntimeStabilitySuiteError(f"expected result fails visible gates for {case.case_id}")
        contract_size = len(_canonical_bytes(case_raw["contract"]))
        if not MIN_CONTRACT_BYTES <= contract_size <= MAX_CONTRACT_BYTES:
            raise RuntimeStabilitySuiteError(
                f"contract size outside the fixed workload envelope for {case.case_id}: {contract_size}"
            )
        contract_sizes.append(contract_size)

    public_case_ids = {case.case_id for case in public_reference.cases}
    public_contract_ids = {case.contract.contract_id for case in public_reference.cases}
    if public_case_ids & {case.case_id for case in suite.cases}:
        raise RuntimeStabilitySuiteError("case IDs overlap the existing public example")
    if public_contract_ids & {case.contract.contract_id for case in suite.cases}:
        raise RuntimeStabilitySuiteError("contract IDs overlap the existing public example")
    maximum_similarity = max(
        _jaccard(case.contract.objective, reference.contract.objective)
        for case in suite.cases
        for reference in public_reference.cases
    )
    if maximum_similarity >= MAX_OBJECTIVE_TOKEN_JACCARD:
        raise RuntimeStabilitySuiteError("objective similarity to the public example exceeds the novelty bound")

    serialized = json.dumps(suite_raw, ensure_ascii=False).casefold()
    forbidden_markers = (
        "candidate-04",
        "candidate_04",
        "protected-multimodel-holdout",
        "protected_case",
    )
    if any(marker in serialized for marker in forbidden_markers):
        raise RuntimeStabilitySuiteError("suite contains a protected-evaluation marker")

    return {
        "valid": True,
        "suite_id": suite.suite_id,
        "suite_canonical_sha256": suite.sha256(),
        "case_count": len(suite.cases),
        "domain_counts": {domain: counts[domain] for domain in DOMAINS},
        "interleaved_order": True,
        "semantic_assertion_count": assertion_count,
        "contract_bytes": {
            "minimum": min(contract_sizes),
            "maximum": max(contract_sizes),
        },
        "maximum_public_objective_token_jaccard": round(maximum_similarity, 6),
        "timeout_seconds": manifest["timeout_seconds"],
        "smoke_repetitions": manifest["smoke_repetitions"],
        "soak_repetitions": manifest["soak_repetitions"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--public-reference", type=Path, default=DEFAULT_PUBLIC_REFERENCE)
    args = parser.parse_args(argv)
    try:
        result = validate_runtime_stability_suite(args.suite, args.manifest, args.public_reference)
    except (RuntimeStabilitySuiteError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
