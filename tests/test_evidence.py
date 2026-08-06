import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_dohaa.evidence.ledger import (
    EvidenceLedger,
    LedgerIntegrityError,
    LedgerReadOnlyError,
)


class EvidenceLedgerTests(unittest.TestCase):
    def test_chain_verifies_and_filters_by_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with EvidenceLedger(path) as ledger:
                ledger.append("run-a", "one", {"value": 1})
                ledger.append("run-b", "two", {"value": 2})
                self.assertTrue(ledger.verify_chain())
                self.assertEqual(len(list(ledger.records("run-a"))), 1)
                self.assertEqual(ledger.record_count(), 2)
                self.assertEqual(ledger.record_count("run-a"), 1)
                self.assertEqual(ledger.run_ids(), ("run-a", "run-b"))

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

    def test_read_only_mode_rejects_append_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with EvidenceLedger(path) as ledger:
                ledger.append("run-a", "one", {"value": 1})

            before = path.stat()
            files_before = sorted(item.name for item in path.parent.iterdir())

            with EvidenceLedger(path, read_only=True) as ledger:
                self.assertTrue(ledger.verify_chain())
                with self.assertRaises(LedgerReadOnlyError):
                    ledger.append("run-b", "two", {"value": 2})

            after = path.stat()
            files_after = sorted(item.name for item in path.parent.iterdir())
            self.assertEqual(after.st_size, before.st_size)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(files_after, files_before)

    def test_read_only_mode_does_not_create_missing_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"

            with self.assertRaises(FileNotFoundError):
                EvidenceLedger(path, read_only=True)

            self.assertFalse(path.exists())

    def test_read_only_mode_rejects_wal_companions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with EvidenceLedger(path) as ledger:
                ledger.append("run-a", "one", {"value": 1})

            wal_path = Path(f"{path}-wal")
            wal_path.write_bytes(b"simulated pending WAL")

            with self.assertRaisesRegex(ValueError, "quiescent ledger"):
                EvidenceLedger(path, read_only=True)


if __name__ == "__main__":
    unittest.main()
