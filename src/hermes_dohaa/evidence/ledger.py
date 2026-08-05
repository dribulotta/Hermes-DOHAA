"""Append-only, hash-chained SQLite evidence ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


class LedgerIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    sequence: int
    run_id: str
    event_type: str
    payload: Any
    created_at: str
    previous_hash: str
    event_hash: str


class EvidenceLedger:
    """A small evidence log whose hash chain makes later mutation detectable."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EvidenceLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def append(self, run_id: str, event_type: str, payload: Any) -> LedgerRecord:
        payload_json = _canonical_json(payload)
        created_at = datetime.now(UTC).isoformat()
        row = self._connection.execute(
            "SELECT event_hash FROM ledger_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = row["event_hash"] if row else "0" * 64
        event_hash = _event_hash(run_id, event_type, payload_json, created_at, previous_hash)
        cursor = self._connection.execute(
            """
            INSERT INTO ledger_events
                (run_id, event_type, payload_json, created_at, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, event_type, payload_json, created_at, previous_hash, event_hash),
        )
        self._connection.commit()
        return LedgerRecord(
            sequence=int(cursor.lastrowid),
            run_id=run_id,
            event_type=event_type,
            payload=json.loads(payload_json),
            created_at=created_at,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def records(self, run_id: str | None = None) -> Iterator[LedgerRecord]:
        if run_id is None:
            rows = self._connection.execute("SELECT * FROM ledger_events ORDER BY sequence")
        else:
            rows = self._connection.execute(
                "SELECT * FROM ledger_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            )
        for row in rows:
            yield LedgerRecord(
                sequence=row["sequence"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
                previous_hash=row["previous_hash"],
                event_hash=row["event_hash"],
            )

    def verify_chain(self) -> bool:
        expected_previous = "0" * 64
        for row in self._connection.execute("SELECT * FROM ledger_events ORDER BY sequence"):
            if row["previous_hash"] != expected_previous:
                raise LedgerIntegrityError(f"Broken previous_hash at sequence {row['sequence']}")
            expected = _event_hash(
                row["run_id"],
                row["event_type"],
                row["payload_json"],
                row["created_at"],
                row["previous_hash"],
            )
            if row["event_hash"] != expected:
                raise LedgerIntegrityError(f"Invalid event_hash at sequence {row['sequence']}")
            expected_previous = row["event_hash"]
        return True


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"ledger payload must be JSON serializable: {exc}") from exc


def _event_hash(
    run_id: str,
    event_type: str,
    payload_json: str,
    created_at: str,
    previous_hash: str,
) -> str:
    envelope = _canonical_json(
        {
            "run_id": run_id,
            "event_type": event_type,
            "payload_json": payload_json,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
    )
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()
