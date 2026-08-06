"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    RequiredEvidenceGate,
    ResultEqualsGate,
)
from hermes_dohaa.contracts.models import ContractError, TaskContract
from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.hermes_api import HermesApiRuntime


_REASONING_EFFORT_CHOICES = ("none", "minimal", "low", "medium", "high", "xhigh")


def _add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_reasoning_effort: str | None,
) -> None:
    parser.add_argument("--hermes-url", default="http://127.0.0.1:8642")
    parser.add_argument("--hermes-model", default="hermes-agent")
    parser.add_argument(
        "--reasoning-effort",
        choices=_REASONING_EFFORT_CHOICES,
        default=default_reasoning_effort,
    )
    parser.add_argument("--hermes-timeout-seconds", type=float, default=300.0)


def _runtime_from_args(args: argparse.Namespace) -> HermesApiRuntime:
    return HermesApiRuntime(
        base_url=args.hermes_url,
        model=args.hermes_model,
        timeout_seconds=args.hermes_timeout_seconds,
        reasoning_effort=args.reasoning_effort,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-dohaa")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a task-contract JSON file")
    validate.add_argument("contract", type=Path)

    run = subparsers.add_parser("run", help="Run a task contract through Hermes and DOHAA gates")
    run.add_argument("contract", type=Path)
    _add_runtime_arguments(run, default_reasoning_effort=None)
    run.add_argument("--ledger", type=Path, default=Path(".dohaa/evidence.sqlite3"))
    run.add_argument("--human-approved", action="store_true")

    smoke = subparsers.add_parser(
        "smoke", help="Run a non-mutating live integration test against Hermes"
    )
    _add_runtime_arguments(smoke, default_reasoning_effort="none")
    smoke.add_argument("--ledger", type=Path, default=Path(".dohaa/smoke.sqlite3"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "smoke":
        return _run_smoke(args)

    try:
        contract = TaskContract.from_json_file(args.contract)
    except ContractError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.command == "validate":
        print(json.dumps({"valid": True, "contract_id": contract.contract_id}))
        return 0

    runtime = _runtime_from_args(args)
    gates = (ActionPolicyGate(), ClaimEvidenceGate(), RequiredEvidenceGate())
    with EvidenceLedger(args.ledger) as ledger:
        result = DohaaController(runtime, gates, ledger).run(
            contract, human_approved=args.human_approved
        )
        ledger.verify_chain()
    payload = asdict(result)
    payload["status"] = result.status.value
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if result.status.value == "succeeded" else 1


def _run_smoke(args: argparse.Namespace) -> int:
    nonce = secrets.token_hex(16)
    expected = {"marker": "DOHAA_SMOKE_OK", "nonce": nonce}
    contract = TaskContract.from_dict(
        {
            "schema_version": "1.0",
            "contract_id": f"hermes-connectivity-smoke-{nonce}",
            "objective": (
                "Return the exact expected_result JSON value. Do not call tools, read files, "
                "access the network, or request actions."
            ),
            "inputs": {"expected_result": expected},
            "constraints": [
                "Return only the proposal JSON required by the system message.",
                "Copy expected_result exactly into result.",
                "Return empty claims, evidence, and requested_actions arrays.",
                "Do not call any tool.",
            ],
            "acceptance_criteria": [
                {
                    "criterion_id": "exact-marker",
                    "description": "The result exactly matches the controller-owned nonce.",
                    "required_evidence": [],
                },
                {
                    "criterion_id": "no-actions",
                    "description": "The proposal requests no action.",
                    "required_evidence": [],
                },
            ],
            "allowed_actions": [],
            "forbidden_actions": [
                "artifact.read",
                "external.publish",
                "filesystem.read",
                "filesystem.write",
                "network.access",
                "shell.execute",
                "terminal.execute",
            ],
            "risk_level": "low",
            "max_attempts": 2,
            "requires_human_approval": False,
        }
    )
    runtime = _runtime_from_args(args)
    gates = (
        ResultEqualsGate(expected),
        ActionPolicyGate(),
        ClaimEvidenceGate(),
        RequiredEvidenceGate(),
    )
    with EvidenceLedger(args.ledger) as ledger:
        result = DohaaController(runtime, gates, ledger).run(contract)
        chain_valid = ledger.verify_chain()
    payload = asdict(result)
    payload["status"] = result.status.value
    payload["smoke"] = {
        "marker_verified": result.proposal is not None and result.proposal.result == expected,
        "no_actions_requested": result.proposal is not None
        and not result.proposal.requested_actions,
        "ledger_chain_valid": chain_valid,
        "scope": "connectivity-and-control-plane-only",
        "runtime_policy": {
            "model": runtime.model,
            "reasoning_effort": runtime.reasoning_effort,
            "timeout_seconds": runtime.timeout_seconds,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
