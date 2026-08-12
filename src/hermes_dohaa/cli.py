"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from hermes_dohaa import __version__
from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    RequiredEvidenceGate,
    ResultEqualsGate,
    ResultSpecGate,
    SemanticAssertionsGate,
)
from hermes_dohaa.assurance.result_spec import parse_result_spec
from hermes_dohaa.assurance.semantic_assertions import (
    parse_semantic_assertions,
)
from hermes_dohaa.contracts.models import ContractError, TaskContract
from hermes_dohaa.controller.engine import (
    DohaaController,
    RunResumeError,
    RunResumeErrorCode,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger, LedgerIntegrityError
from hermes_dohaa.evaluation import (
    EvaluationProtocol,
    EvaluationProtocolError,
    EvaluationSuite,
    EvaluationSuiteError,
    ModelManifest,
    ModelManifestError,
    MultimodelEvaluationError,
    SuiteCommitment,
    SuiteCommitmentError,
    aggregate_model_slot_checkpoints,
    freeze_model_manifest,
    run_comparative_evaluation,
    run_model_slot_evaluation,
    run_multimodel_evaluation,
    write_evaluation_result,
    write_model_manifest,
    write_suite_commitment,
)
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
    run.add_argument(
        "--resume-run-id",
        help=(
            "Resume an approval.required checkpoint in the existing ledger; "
            "requires --human-approved"
        ),
    )

    smoke = subparsers.add_parser(
        "smoke", help="Run a non-mutating live integration test against Hermes"
    )
    _add_runtime_arguments(smoke, default_reasoning_effort="none")
    smoke.add_argument("--ledger", type=Path, default=Path(".dohaa/smoke.sqlite3"))

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Compare direct, self-reflective, and DOHAA-controlled responses",
    )
    evaluate.add_argument("suite", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--seed", type=int, default=0)
    evaluate.add_argument("--repetitions", type=int, default=1)
    evaluate.add_argument(
        "--model-artifact-id",
        help="Pinned model artifact identifier recorded with the evaluation",
    )
    evaluate.add_argument("--temperature", type=float, default=0.0)
    evaluate.add_argument("--top-p", type=float, default=1.0)
    evaluate.add_argument("--sampling-seed", type=int, default=0)
    evaluate.add_argument(
        "--suite-commitment",
        type=Path,
        help="Verify a previously frozen protected-suite commitment",
    )
    _add_runtime_arguments(evaluate, default_reasoning_effort="none")

    freeze_suite = subparsers.add_parser(
        "freeze-suite",
        help="Freeze a private 30-50 case holdout before evaluating it",
    )
    freeze_suite.add_argument("suite", type=Path)
    freeze_suite.add_argument("--output", type=Path, required=True)
    freeze_suite.add_argument("--protocol-commit", required=True)

    validate_protocol = subparsers.add_parser(
        "validate-evaluation-protocol",
        help="Validate and hash a multi-model evaluation preregistration",
    )
    validate_protocol.add_argument("protocol", type=Path)

    freeze_manifest = subparsers.add_parser(
        "freeze-model-manifest",
        help="Freeze exact model and server identities before suite authorship",
    )
    freeze_manifest.add_argument("draft", type=Path)
    freeze_manifest.add_argument("--protocol", type=Path, required=True)
    freeze_manifest.add_argument("--output", type=Path, required=True)

    multimodel = subparsers.add_parser(
        "evaluate-multimodel",
        help="Run a frozen suite across every preregistered model",
    )
    multimodel.add_argument("suite", type=Path)
    multimodel.add_argument("--suite-commitment", type=Path, required=True)
    multimodel.add_argument("--protocol", type=Path, required=True)
    multimodel.add_argument("--model-manifest", type=Path, required=True)
    multimodel.add_argument("--output", type=Path, required=True)
    multimodel.add_argument(
        "--hermes-url",
        default="http://127.0.0.1:8642",
        help="Shared OpenAI-compatible endpoint for the frozen model aliases",
    )

    model_slot = subparsers.add_parser(
        "evaluate-model-slot",
        help="Run exactly one preregistered model and write a private checkpoint",
    )
    model_slot.add_argument("suite", type=Path)
    model_slot.add_argument("--suite-commitment", type=Path, required=True)
    model_slot.add_argument("--protocol", type=Path, required=True)
    model_slot.add_argument("--model-manifest", type=Path, required=True)
    model_slot.add_argument("--slot-id", required=True)
    model_slot.add_argument("--execution-code-commit", required=True)
    model_slot.add_argument("--output", type=Path, required=True)
    model_slot.add_argument("--hermes-url", default="http://127.0.0.1:8642")

    aggregate_slots = subparsers.add_parser(
        "aggregate-multimodel",
        help="Verify and aggregate private model-slot checkpoints offline",
    )
    aggregate_slots.add_argument("suite", type=Path)
    aggregate_slots.add_argument("checkpoints", type=Path, nargs="+")
    aggregate_slots.add_argument("--suite-commitment", type=Path, required=True)
    aggregate_slots.add_argument("--protocol", type=Path, required=True)
    aggregate_slots.add_argument("--model-manifest", type=Path, required=True)
    aggregate_slots.add_argument("--execution-code-commit", required=True)
    aggregate_slots.add_argument("--output", type=Path, required=True)

    verify_ledger = subparsers.add_parser(
        "verify-ledger",
        help="Verify an evidence ledger offline without modifying it",
    )
    verify_ledger.add_argument("ledger", type=Path)
    verify_ledger.add_argument(
        "--run-id",
        help="Report events for one run after verifying the complete chain",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "smoke":
        return _run_smoke(args)
    if args.command == "verify-ledger":
        return _run_verify_ledger(args)
    if args.command == "evaluate":
        return _run_evaluate(args)
    if args.command == "freeze-suite":
        return _run_freeze_suite(args)
    if args.command == "validate-evaluation-protocol":
        return _run_validate_evaluation_protocol(args)
    if args.command == "freeze-model-manifest":
        return _run_freeze_model_manifest(args)
    if args.command == "evaluate-multimodel":
        return _run_evaluate_multimodel(args)
    if args.command == "evaluate-model-slot":
        return _run_evaluate_model_slot(args)
    if args.command == "aggregate-multimodel":
        return _run_aggregate_multimodel(args)

    try:
        contract = TaskContract.from_json_file(args.contract)
        _validate_contract_gate_inputs(contract)
    except (ContractError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.command == "validate":
        print(json.dumps({"valid": True, "contract_id": contract.contract_id}))
        return 0

    runtime = _runtime_from_args(args)
    gates = _contract_gates(contract)
    if args.resume_run_id is None:
        with EvidenceLedger(args.ledger) as ledger:
            result = DohaaController(runtime, gates, ledger).run(
                contract,
                human_approved=args.human_approved,
            )
            ledger.verify_chain()
        payload = asdict(result)
        payload["status"] = result.status.value
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0 if result.status.value == "succeeded" else 1

    try:
        with EvidenceLedger(args.ledger, create=False) as ledger:
            controller = DohaaController(runtime, gates, ledger)
            result = controller.resume(
                contract,
                args.resume_run_id,
                human_approved=args.human_approved,
            )
            ledger.verify_chain()
    except RunResumeError as exc:
        payload = {
            "status": "failed",
            "run_id": args.resume_run_id,
            "reason_code": exc.code.value,
            "reason": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    except LedgerIntegrityError as exc:
        payload = {
            "status": "failed",
            "run_id": args.resume_run_id,
            "reason_code": RunResumeErrorCode.CHECKPOINT_INVALID.value,
            "reason": f"evidence ledger integrity check failed: {exc}",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    except FileNotFoundError as exc:
        payload = {
            "status": "failed",
            "run_id": args.resume_run_id,
            "reason_code": RunResumeErrorCode.NOT_FOUND.value,
            "reason": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        payload = {
            "status": "failed",
            "run_id": args.resume_run_id,
            "reason_code": RunResumeErrorCode.CHECKPOINT_INVALID.value,
            "reason": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    payload = asdict(result)
    payload["status"] = result.status.value
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if result.status.value == "succeeded" else 1


def _contract_gates(contract: TaskContract):
    gates = []
    if "result_spec" in contract.inputs:
        gates.append(ResultSpecGate())
    if "semantic_assertions" in contract.inputs:
        gates.append(SemanticAssertionsGate())
    gates.extend(
        (ActionPolicyGate(), ClaimEvidenceGate(), RequiredEvidenceGate())
    )
    return tuple(gates)


def _validate_contract_gate_inputs(contract: TaskContract) -> None:
    if "result_spec" in contract.inputs:
        try:
            parse_result_spec(contract.inputs["result_spec"])
        except ValueError as exc:
            raise ValueError(f"invalid result_spec: {exc}") from exc
    if "semantic_assertions" in contract.inputs:
        try:
            parse_semantic_assertions(
                contract.inputs["semantic_assertions"]
            )
        except ValueError as exc:
            raise ValueError(
                f"invalid semantic_assertions: {exc}"
            ) from exc


def _run_evaluate(args: argparse.Namespace) -> int:
    try:
        suite = EvaluationSuite.from_json_file(args.suite)
        commitment = None
        if args.suite_commitment is not None:
            commitment = SuiteCommitment.from_json_file(
                args.suite_commitment
            )
            commitment.verify(suite)

        def runtime_factory(contract, session_id, trial_sampling_seed):
            del contract
            return HermesApiRuntime(
                base_url=args.hermes_url,
                model=args.hermes_model,
                timeout_seconds=args.hermes_timeout_seconds,
                session_id=session_id,
                reasoning_effort=args.reasoning_effort,
                temperature=args.temperature,
                top_p=args.top_p,
                sampling_seed=trial_sampling_seed,
            )

        result = run_comparative_evaluation(
            suite,
            runtime_factory,
            seed=args.seed,
            repetitions=args.repetitions,
            sampling_seed=args.sampling_seed,
            runtime_policy={
                "adapter": "hermes_api",
                "hermes_dohaa_version": __version__,
                "hermes_url": args.hermes_url,
                "model_alias": args.hermes_model,
                "model_artifact_id": args.model_artifact_id,
                "reasoning_effort": args.reasoning_effort,
                "temperature": args.temperature,
                "top_p": args.top_p,
                "sampling_seed": args.sampling_seed,
                "timeout_seconds": args.hermes_timeout_seconds,
            },
            suite_commitment=(
                commitment.to_dict()
                if commitment is not None
                else None
            ),
        )
        write_evaluation_result(args.output, result)
    except (
        EvaluationSuiteError,
        SuiteCommitmentError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        "status": "completed",
        "evaluation_id": result["evaluation_id"],
        "suite_id": result["suite_id"],
        "suite_sha256": result["suite_sha256"],
        "seed": result["seed"],
        "repetitions": result["repetitions"],
        "runtime_policy": result["runtime_policy"],
        "suite_commitment": result["suite_commitment"],
        "suite_commitment_sha256": result["suite_commitment_sha256"],
        "output": str(args.output),
        "summary": result["summary"],
        "statistical_analysis": result["statistical_analysis"],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_freeze_suite(args: argparse.Namespace) -> int:
    try:
        suite = EvaluationSuite.from_json_file(args.suite)
        commitment = SuiteCommitment.create(
            suite,
            protocol_commit=args.protocol_commit,
        )
        write_suite_commitment(args.output, commitment)
    except (
        EvaluationSuiteError,
        SuiteCommitmentError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        "status": "frozen",
        "commitment": commitment.to_dict(),
        "commitment_sha256": commitment.sha256(),
        "output": str(args.output),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_validate_evaluation_protocol(args: argparse.Namespace) -> int:
    try:
        protocol = EvaluationProtocol.from_json_file(args.protocol)
    except (EvaluationProtocolError, OSError, ValueError) as exc:
        payload = {
            "valid": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        "valid": True,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.sha256(),
        "model_slots": len(protocol.model_slots),
        "case_count": protocol.suite_policy["case_count"],
        "domain_counts": dict(protocol.suite_policy["domain_counts"]),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_freeze_model_manifest(args: argparse.Namespace) -> int:
    try:
        protocol = EvaluationProtocol.from_json_file(args.protocol)
        manifest = freeze_model_manifest(protocol, args.draft)
        write_model_manifest(args.output, manifest)
    except (
        EvaluationProtocolError,
        ModelManifestError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        "status": "frozen",
        "manifest_id": manifest.manifest_id,
        "protocol_id": manifest.protocol_id,
        "protocol_sha256": manifest.protocol_sha256,
        "model_count": len(manifest.models),
        "manifest_sha256": manifest.sha256(),
        "output": str(args.output),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_evaluate_multimodel(args: argparse.Namespace) -> int:
    try:
        if args.output.exists():
            raise FileExistsError(
                f"multi-model evaluation output already exists: {args.output}"
            )
        protocol = EvaluationProtocol.from_json_file(args.protocol)
        manifest = ModelManifest.from_json_file(args.model_manifest)
        suite = EvaluationSuite.from_json_file(args.suite)
        commitment = SuiteCommitment.from_json_file(args.suite_commitment)
        execution = protocol.execution_policy

        def runtime_factory_builder(model):
            def runtime_factory(contract, session_id, trial_sampling_seed):
                del contract
                return HermesApiRuntime(
                    base_url=args.hermes_url,
                    model=model.model_alias,
                    timeout_seconds=execution["timeout_seconds"],
                    session_id=session_id,
                    reasoning_effort=execution["reasoning_effort"],
                    temperature=execution["temperature"],
                    top_p=execution["top_p"],
                    sampling_seed=trial_sampling_seed,
                )

            return runtime_factory

        result = run_multimodel_evaluation(
            suite,
            commitment,
            protocol,
            manifest,
            runtime_factory_builder,
            runtime_context={
                "adapter": "hermes_api",
                "hermes_dohaa_version": __version__,
                "hermes_url": args.hermes_url,
            },
        )
        write_evaluation_result(args.output, result)
    except (
        EvaluationProtocolError,
        EvaluationSuiteError,
        ModelManifestError,
        MultimodelEvaluationError,
        SuiteCommitmentError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        "status": "completed",
        "evaluation_id": result["evaluation_id"],
        "protocol_id": result["protocol_id"],
        "protocol_sha256": result["protocol_sha256"],
        "model_manifest_sha256": result["model_manifest_sha256"],
        "suite_id": result["suite_id"],
        "suite_sha256": result["suite_sha256"],
        "output": str(args.output),
        "aggregate_analysis": result["aggregate_analysis"],
        "success_assessment": result["success_assessment"],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_evaluate_model_slot(args: argparse.Namespace) -> int:
    try:
        if args.output.exists():
            raise FileExistsError(f"checkpoint output already exists: {args.output}")
        protocol = EvaluationProtocol.from_json_file(args.protocol)
        manifest = ModelManifest.from_json_file(args.model_manifest)
        suite = EvaluationSuite.from_json_file(args.suite)
        commitment = SuiteCommitment.from_json_file(args.suite_commitment)
        execution = protocol.execution_policy

        def runtime_factory_builder(model):
            def runtime_factory(contract, session_id, trial_sampling_seed):
                del contract
                return HermesApiRuntime(
                    base_url=args.hermes_url,
                    model=model.model_alias,
                    timeout_seconds=execution["timeout_seconds"],
                    session_id=session_id,
                    reasoning_effort=execution["reasoning_effort"],
                    temperature=execution["temperature"],
                    top_p=execution["top_p"],
                    sampling_seed=trial_sampling_seed,
                )
            return runtime_factory

        checkpoint = run_model_slot_evaluation(
            suite, commitment, protocol, manifest, args.slot_id,
            runtime_factory_builder,
            runtime_context={
                "adapter": "hermes_api",
                "hermes_dohaa_version": __version__,
            },
            execution_code_commit=args.execution_code_commit,
        )
        write_evaluation_result(args.output, checkpoint)
    except (
        EvaluationProtocolError, EvaluationSuiteError, ModelManifestError,
        MultimodelEvaluationError, SuiteCommitmentError, OSError, ValueError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "completed",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "protocol_id": checkpoint["protocol_id"],
        "slot_id": checkpoint["slot_id"],
        "model_artifact_id": checkpoint["model_artifact_id"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


def _run_aggregate_multimodel(args: argparse.Namespace) -> int:
    try:
        if args.output.exists():
            raise FileExistsError(f"aggregate output already exists: {args.output}")
        protocol = EvaluationProtocol.from_json_file(args.protocol)
        manifest = ModelManifest.from_json_file(args.model_manifest)
        suite = EvaluationSuite.from_json_file(args.suite)
        commitment = SuiteCommitment.from_json_file(args.suite_commitment)
        checkpoints = []
        for path in args.checkpoints:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("checkpoint root must be an object")
            checkpoints.append(value)
        result = aggregate_model_slot_checkpoints(
            suite, commitment, protocol, manifest, checkpoints,
            execution_code_commit=args.execution_code_commit,
        )
        write_evaluation_result(args.output, result)
    except (
        EvaluationProtocolError, EvaluationSuiteError, ModelManifestError,
        MultimodelEvaluationError, SuiteCommitmentError, OSError, ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "completed",
        "evaluation_id": result["evaluation_id"],
        "protocol_id": result["protocol_id"],
        "model_manifest_sha256": result["model_manifest_sha256"],
        "suite_id": result["suite_id"],
        "output": str(args.output),
        "aggregate_analysis": result["aggregate_analysis"],
        "success_assessment": result["success_assessment"],
    }, ensure_ascii=False))
    return 0


def _run_verify_ledger(args: argparse.Namespace) -> int:
    ledger_path = args.ledger
    selected_run_id = args.run_id
    base_payload = {
        "ledger": str(ledger_path),
        "chain_scope": "entire-ledger",
        "read_only": True,
    }

    if selected_run_id is not None and not selected_run_id.strip():
        payload = {
            **base_payload,
            "valid": False,
            "error_type": "InvalidRunId",
            "error": "--run-id must be a non-empty string",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    try:
        with EvidenceLedger(ledger_path, read_only=True) as ledger:
            ledger.verify_chain()
            event_count = ledger.record_count()
            run_ids = ledger.run_ids()
            selected_event_count = (
                ledger.record_count(selected_run_id)
                if selected_run_id is not None
                else None
            )
    except LedgerIntegrityError as exc:
        payload = {
            **base_payload,
            "valid": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, ValueError) as exc:
        payload = {
            **base_payload,
            "valid": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    payload = {
        **base_payload,
        "valid": True,
        "event_count": event_count,
        "run_count": len(run_ids),
        "run_ids": list(run_ids),
        "selected_run_id": selected_run_id,
        "selected_event_count": selected_event_count,
    }

    if selected_run_id is not None and selected_event_count == 0:
        payload["selection_found"] = False
        payload["error_type"] = "RunNotFound"
        payload["error"] = f"Run ID not found: {selected_run_id}"
        print(json.dumps(payload, ensure_ascii=False))
        return 3

    if selected_run_id is not None:
        payload["selection_found"] = True

    print(json.dumps(payload, ensure_ascii=False))
    return 0


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
