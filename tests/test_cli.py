import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.cli import main
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


if __name__ == "__main__":
    unittest.main()
