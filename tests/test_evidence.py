import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_dohaa.evidence.ledger import EvidenceLedger, LedgerIntegrityError


class EvidenceLedgerTests(unittest.TestCase):
    def test_chain_verifies_and_filters_by_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with EvidenceLedger(path) as ledger:
                ledger.append("run-a", "one", {"value": 1})
                ledger.append("run-b", "two", {"value": 2})
                self.assertTrue(ledger.verify_chain())
                self.assertEqual(len(list(ledger.records("run-a"))), 1)

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with EvidenceLedger(path) as ledger:
                ledger.append("run-a", "one", {"value": 1})
            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE ledger_events SET payload_json = ? WHERE sequence = 1",
                ('{"value":999}',),
            )
            connection.commit()
            connection.close()
            with EvidenceLedger(path) as ledger:
                with self.assertRaises(LedgerIntegrityError):
                    ledger.verify_chain()


if __name__ == "__main__":
    unittest.main()
