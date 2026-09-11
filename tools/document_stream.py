"""Offline public-development stream world, not a production actuator or agent.

Revision order is source-owned; arrival order is simulator-owned. Database
transactions cover deliveries only, never external effects or model requests.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable


def identifier(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is not None


def nonnegative_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


@dataclass(frozen=True)
class DocumentEvent:
    event_id: str
    source_id: str
    revision: int
    arrived_at: int
    text: str
    expires_at: int | None = None
    retracted: bool = False

    def __post_init__(self):
        if not identifier(self.event_id) or not identifier(self.source_id):
            raise ValueError("invalid event or source identifier")
        if not nonnegative_integer(self.revision) or self.revision == 0:
            raise ValueError("revision must be a positive bounded integer")
        if not nonnegative_integer(self.arrived_at):
            raise ValueError("arrival must be a nonnegative bounded tick")
        if self.expires_at is not None and not nonnegative_integer(self.expires_at):
            raise ValueError("invalid expiry tick")
        if type(self.text) is not str or len(self.text.encode("utf-8")) > 65536:
            raise ValueError("invalid or oversized document text")
        if type(self.retracted) is not bool:
            raise ValueError("retracted must be boolean")
        if self.retracted:
            if self.text or self.expires_at is not None:
                raise ValueError("retractions contain neither text nor expiry")
        elif not self.text.strip():
            raise ValueError("an active document requires text")

    def encoded(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def revision_content(self) -> tuple:
        return self.source_id, self.revision, self.text, self.expires_at, self.retracted


class StreamStore:
    """Append-only through this API; run-bound, restartable synthetic deliveries.

    The caller owns the local database and its directory. This is not a security
    boundary against local writers, a signed evidence ledger, or crash-proof
    execution of external actions. Context-manager use closes connections.
    """

    def __init__(self, path: Path, *, run_id: str):
        if not identifier(run_id):
            raise ValueError("invalid run identifier")
        self.run_id = run_id
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("CREATE TABLE IF NOT EXISTS stream_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            metadata = dict(self.db.execute("SELECT key, value FROM stream_meta"))
            expected = {"schema": "hermes-document-stream/1.0", "run_id": run_id}
            if metadata and metadata != expected:
                raise ValueError("stream schema or run identity mismatch")
            if not metadata:
                self.db.executemany("INSERT INTO stream_meta VALUES (?, ?)", expected.items())
            self.db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                source_id TEXT NOT NULL, revision INTEGER NOT NULL,
                arrived_at INTEGER NOT NULL, payload TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS source_revision ON deliveries(source_id, revision)")
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def append(self, events: Iterable[DocumentEvent]) -> int:
        """Atomically record <=256 arrivals, with <=10,000 records per dev run.

        An exact event redelivery is a no-op, including after an uncertain caller
        acknowledgement. Conflicting event IDs or same-source revisions reject
        the whole batch. New arrivals cannot backdate an existing delivery.
        """
        inserted = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            count, last_tick = self.db.execute(
                "SELECT COUNT(*), COALESCE(MAX(arrived_at), 0) FROM deliveries"
            ).fetchone()
            for position, event in enumerate(events, 1):
                if position > 256 or type(event) is not DocumentEvent:
                    raise ValueError("invalid or oversized delivery batch")
                encoded = event.encoded()
                prior = self.db.execute("SELECT payload FROM deliveries WHERE event_id=?", (event.event_id,)).fetchone()
                if prior is not None:
                    if prior[0] != encoded:
                        raise ValueError("conflicting delivery identity")
                    continue
                if count + inserted >= 10000 or event.arrived_at < last_tick:
                    raise ValueError("stream capacity or arrival order violated")
                revision = self.db.execute(
                    "SELECT payload FROM deliveries WHERE source_id=? AND revision=? LIMIT 1",
                    (event.source_id, event.revision),
                ).fetchone()
                if revision and DocumentEvent(**json.loads(revision[0])).revision_content() != event.revision_content():
                    raise ValueError("conflicting source revision")
                self.db.execute(
                    "INSERT INTO deliveries(event_id,source_id,revision,arrived_at,payload) VALUES (?,?,?,?,?)",
                    (event.event_id, event.source_id, event.revision, event.arrived_at, encoded),
                )
                inserted += 1
                last_tick = event.arrived_at
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return inserted

    def observation(self, at_tick: int) -> dict:
        """Only delivered documents and public metadata; no reference answers."""
        if not nonnegative_integer(at_tick):
            raise ValueError("invalid observation tick")
        rows = self.db.execute(
            "SELECT payload FROM deliveries WHERE arrived_at<=? ORDER BY sequence", (at_tick,)
        ).fetchall()
        return {"run_id": self.run_id, "at_tick": at_tick,
                "events": [json.loads(row[0]) for row in rows]}

    def active_sources(self, at_tick: int) -> dict[str, DocumentEvent]:
        """Temporal reducer for the grader or a common helper granted to all arms.

        The latest delivered revision wins even when it has expired or been
        retracted. Older revisions never become current again implicitly.
        This does not resolve semantic contradictions between distinct sources.
        """
        latest = {}
        for payload in self.observation(at_tick)["events"]:
            event = DocumentEvent(**payload)
            previous = latest.get(event.source_id)
            if previous is None or event.revision > previous.revision:
                latest[event.source_id] = event
        return {key: event for key, event in sorted(latest.items())
                if not event.retracted and (event.expires_at is None or at_tick < event.expires_at)}
