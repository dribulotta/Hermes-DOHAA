"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from hermes_dohaa.assurance.gates import ActionPolicyGate, ClaimEvidenceGate, RequiredEvidenceGate
from hermes_dohaa.contracts.models import ContractError, TaskContract
from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.hermes_api import HermesApiRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-dohaa")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a task-contract JSON file")
    validate.add_argument("contract", type=Path)

    run = subparsers.add_parser("run", help="Run a task contract through Hermes and DOHAA gates")
    run.add_argument("contract", type=Path)
    run.add_argument("--hermes-url", default="http://127.0.0.1:8642")
    run.add_argument("--ledger", type=Path, default=Path(".dohaa/evidence.sqlite3"))
    run.add_argument("--human-approved", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = TaskContract.from_json_file(args.contract)
    except ContractError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.command == "validate":
        print(json.dumps({"valid": True, "contract_id": contract.contract_id}))
        return 0

    runtime = HermesApiRuntime(base_url=args.hermes_url)
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


if __name__ == "__main__":
    raise SystemExit(main())
