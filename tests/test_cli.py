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
    del runtime, feedback
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
                exit_code = main(["smoke", "--ledger", str(ledger)])

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
            },
        )


if __name__ == "__main__":
    unittest.main()
