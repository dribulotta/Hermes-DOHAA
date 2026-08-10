import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    RequiredEvidenceGate,
)
from hermes_dohaa.cli import main
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Claim, EvidenceItem, Proposal


def exact_smoke_proposal(runtime, contract, feedback):
    del feedback
    exact_smoke_proposal.runtime = runtime
    return Proposal(result=dict(contract.inputs["expected_result"]))


def approval_contract():
    return {
        "schema_version": "1.0",
        "contract_id": "approval-resume-test",
        "objective": "Produce a result that requires explicit approval",
        "acceptance_criteria": [
            {
                "criterion_id": "grounded",
                "description": "The result is grounded",
                "required_evidence": ["source-1"],
            }
        ],
        "allowed_actions": ["artifact.read"],
        "forbidden_actions": ["external.publish"],
        "risk_level": "critical",
        "max_attempts": 2,
        "requires_human_approval": True,
    }


def approval_proposal():
    evidence = EvidenceItem.create(
        "source-1",
        "artifact",
        "fixture",
        {"ok": True},
    )
    return Proposal(
        result={"summary": "verified"},
        claims=(Claim("The fixture says ok", ("source-1",)),),
        evidence=(evidence,),
        requested_actions=("artifact.read",),
    )


class OneProposalRuntime:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = 0

    def propose(self, contract, feedback):
        del contract, feedback
        self.calls += 1
        return self.proposal


class CliTests(unittest.TestCase):
    def test_smoke_command_verifies_nonce_actions_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "smoke.sqlite3"
            output = io.StringIO()
            with patch(
                "hermes_dohaa.runtime.hermes_api.HermesApiRuntime.propose",
                autospec=True,
                side_effect=exact_smoke_proposal,
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "smoke",
                        "--ledger",
                        str(ledger),
                        "--hermes-model",
                        "dohaa-runtime",
                        "--reasoning-effort",
                        "none",
                        "--hermes-timeout-seconds",
                        "45",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(
            payload["smoke"],
            {
                "marker_verified": True,
                "no_actions_requested": True,
                "ledger_chain_valid": True,
                "scope": "connectivity-and-control-plane-only",
                "runtime_policy": {
                    "model": "dohaa-runtime",
                    "reasoning_effort": "none",
                    "timeout_seconds": 45.0,
                },
            },
        )
        runtime = exact_smoke_proposal.runtime
        self.assertEqual(runtime.model, "dohaa-runtime")
        self.assertEqual(runtime.reasoning_effort, "none")
        self.assertEqual(runtime.timeout_seconds, 45.0)

    def test_verify_ledger_reports_valid_summary_and_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "evidence.sqlite3"
            with EvidenceLedger(ledger_path) as ledger:
                ledger.append("run-b", "two", {"value": 2})
                ledger.append("run-a", "one", {"value": 1})
                ledger.append("run-a", "three", {"value": 3})

            before = ledger_path.stat()
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "verify-ledger",
                        str(ledger_path),
                        "--run-id",
                        "run-a",
                    ]
                )
            after = ledger_path.stat()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["valid"])
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["chain_scope"], "entire-ledger")
        self.assertEqual(payload["event_count"], 3)
        self.assertEqual(payload["run_count"], 2)
        self.assertEqual(payload["run_ids"], ["run-a", "run-b"])
        self.assertEqual(payload["selected_run_id"], "run-a")
        self.assertEqual(payload["selected_event_count"], 2)
        self.assertTrue(payload["selection_found"])
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_verify_ledger_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "evidence.sqlite3"
            with EvidenceLedger(ledger_path) as ledger:
                ledger.append("run-a", "one", {"value": 1})

            connection = sqlite3.connect(ledger_path)
            connection.execute(
                "UPDATE ledger_events SET payload_json = ? WHERE sequence = 1",
                ('{"value":999}',),
            )
            connection.commit()
            connection.close()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["verify-ledger", str(ledger_path)])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error_type"], "LedgerIntegrityError")

    def test_verify_ledger_rejects_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.sqlite3"
            invalid = root / "invalid.sqlite3"
            wrong_schema = root / "wrong-schema.sqlite3"

            invalid.write_bytes(b"not a sqlite database")

            connection = sqlite3.connect(wrong_schema)
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.commit()
            connection.close()

            cases = (
                ("missing", missing),
                ("invalid", invalid),
                ("wrong-schema", wrong_schema),
            )

            for label, ledger_path in cases:
                with self.subTest(label=label):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = main(
                            ["verify-ledger", str(ledger_path)]
                        )
                    payload = json.loads(output.getvalue())
                    self.assertEqual(exit_code, 2)
                    self.assertFalse(payload["valid"])

            self.assertFalse(missing.exists())

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "verify-ledger",
                        str(missing),
                        "--run-id",
                        "   ",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertFalse(payload["valid"])
            self.assertEqual(payload["error_type"], "InvalidRunId")
            self.assertFalse(missing.exists())

    def test_verify_ledger_reports_missing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "evidence.sqlite3"
            with EvidenceLedger(ledger_path) as ledger:
                ledger.append("run-a", "one", {"value": 1})

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "verify-ledger",
                        str(ledger_path),
                        "--run-id",
                        "run-missing",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertTrue(payload["valid"])
        self.assertFalse(payload["selection_found"])
        self.assertEqual(payload["selected_event_count"], 0)
        self.assertEqual(payload["error_type"], "RunNotFound")

    def test_run_command_resumes_approval_checkpoint_without_runtime_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "contract.json"
            ledger_path = root / "evidence.sqlite3"
            contract_path.write_text(
                json.dumps(approval_contract()),
                encoding="utf-8",
            )
            contract = TaskContract.from_json_file(contract_path)
            runtime = OneProposalRuntime(approval_proposal())
            gates = (
                ActionPolicyGate(),
                ClaimEvidenceGate(),
                RequiredEvidenceGate(),
            )
            with EvidenceLedger(ledger_path) as ledger:
                pending = DohaaController(runtime, gates, ledger).run(contract)

            output = io.StringIO()
            with patch(
                "hermes_dohaa.runtime.hermes_api.HermesApiRuntime.propose",
                autospec=True,
                side_effect=AssertionError("runtime must not be called"),
            ), redirect_stdout(output):
                exit_code = main(
                    [
                        "run",
                        str(contract_path),
                        "--ledger",
                        str(ledger_path),
                        "--resume-run-id",
                        pending.run_id,
                        "--human-approved",
                    ]
                )

            with EvidenceLedger(ledger_path, create=False) as ledger:
                events = [
                    record.event_type
                    for record in ledger.records(pending.run_id)
                ]

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["reason_code"], "run.succeeded")
        self.assertEqual(payload["run_id"], pending.run_id)
        self.assertEqual(runtime.calls, 1)
        self.assertEqual(events.count("run.resumed"), 1)
        self.assertEqual(events.count("run.finished"), 2)

    def test_run_command_resume_errors_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "contract.json"
            missing_ledger = root / "missing.sqlite3"
            contract_path.write_text(
                json.dumps(approval_contract()),
                encoding="utf-8",
            )

            missing_output = io.StringIO()
            with redirect_stdout(missing_output):
                missing_exit = main(
                    [
                        "run",
                        str(contract_path),
                        "--ledger",
                        str(missing_ledger),
                        "--resume-run-id",
                        "missing-run",
                        "--human-approved",
                    ]
                )

            ledger_path = root / "evidence.sqlite3"
            contract = TaskContract.from_json_file(contract_path)
            gates = (
                ActionPolicyGate(),
                ClaimEvidenceGate(),
                RequiredEvidenceGate(),
            )
            with EvidenceLedger(ledger_path) as ledger:
                pending = DohaaController(
                    OneProposalRuntime(approval_proposal()),
                    gates,
                    ledger,
                ).run(contract)

            approval_output = io.StringIO()
            with redirect_stdout(approval_output):
                approval_exit = main(
                    [
                        "run",
                        str(contract_path),
                        "--ledger",
                        str(ledger_path),
                        "--resume-run-id",
                        pending.run_id,
                    ]
                )

        missing_payload = json.loads(missing_output.getvalue())
        approval_payload = json.loads(approval_output.getvalue())
        self.assertEqual(missing_exit, 2)
        self.assertEqual(missing_payload["reason_code"], "resume.not_found")
        self.assertFalse(missing_ledger.exists())
        self.assertEqual(approval_exit, 2)
        self.assertEqual(
            approval_payload["reason_code"],
            "resume.approval_missing",
        )


if __name__ == "__main__":
    unittest.main()
