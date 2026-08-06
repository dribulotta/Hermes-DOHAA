import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.cli import main
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal


def exact_smoke_proposal(runtime, contract, feedback):
    del feedback
    exact_smoke_proposal.runtime = runtime
    return Proposal(result=dict(contract.inputs["expected_result"]))


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


if __name__ == "__main__":
    unittest.main()
