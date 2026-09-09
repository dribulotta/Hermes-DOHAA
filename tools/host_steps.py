"""Durable trusted-host steps for the standalone synthetic tool experiment.

No generation, execution or effect recovery is performed here. The required
terminal verifier belongs to the trusted coordinator and must retrieve evidence
already verified by the native/model-ownership boundary. Receipt shape is not
authentication. All per-task activity must use that coordinator; independent
stores are not a universal transaction against writers bypassing this API.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Callable

from tools.controlled_tool_proposal import HostStep, ReservationRef, bind_tool_proposal
from tools.controlled_tools import Conflict, IntentJournal, ToolStore

ACTIVE = ('allocated', 'requesting', 'proposed')
ZERO = '0'*64


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise Conflict('invalid_digest')


def _loads(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate_key')
            result[key] = value
        return result
    def nonfinite(_): raise ValueError('nonfinite_number')
    if type(data) is not bytes or not 0 < len(data) <= 262144:
        raise Conflict('invalid_record_bytes')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, RecursionError):
        raise Conflict('invalid_record_json') from None


def _encode_step(step):
    if type(step) is not HostStep: raise Conflict('host_step_required')
    step.__post_init__()
    return _json(asdict(step))


def _decode_step(payload):
    raw = _loads(payload.encode('utf-8'))
    if type(raw) is not dict or set(raw) != set(HostStep.__dataclass_fields__):
        raise Conflict('invalid_stored_scope')
    if any(type(raw[key]) is not list for key in ('allowed_kinds', 'allowed_products', 'reservations')):
        raise Conflict('invalid_stored_scope')
    try:
        raw['allowed_kinds'] = tuple(raw['allowed_kinds'])
        raw['allowed_products'] = tuple(raw['allowed_products'])
        raw['reservations'] = tuple(ReservationRef(**reference) for reference in raw['reservations'])
        step = HostStep(**raw)
        if _encode_step(step) != payload: raise ValueError('noncanonical_scope')
        return step
    except (TypeError, ValueError):
        raise Conflict('invalid_stored_scope') from None


@dataclass(frozen=True)
class RequestBinding:
    operation_id: str
    step_sha256: str
    request_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class TerminalEvidence:
    """Output of a required trusted verifier, never an untrusted tool argument."""
    policy_sha256: str
    receipt_bytes: bytes


class HostStepStore:
    def __init__(self, path: Path, *, journal: IntentJournal, tool: ToolStore,
                 terminal_verifier: Callable[[RequestBinding], TerminalEvidence | None], fault=None):
        if not callable(terminal_verifier): raise ValueError('trusted_verifier_required')
        if not isinstance(journal, IntentJournal) or not isinstance(tool, ToolStore):
            raise ValueError('simulator_stores_required')
        requested = Path(path)
        if requested.is_symlink(): raise ValueError('host_store_symlink')
        self.path = requested.resolve()
        if len({self.path, journal.path.resolve(), tool.path.resolve()}) != 3:
            raise ValueError('separate_databases_required')
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not self.path.is_file(): raise ValueError('regular_host_store_required')
        else:
            os.close(descriptor)
        if os.name == 'posix' and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise ValueError('private_host_store_required')
        self.journal, self.tool = journal, tool
        self._database_path = self.path
        self._database_bindings = {'schema': 'controlled-host-steps/1.0',
                                  'intent_path': str(journal.path.resolve()),
                                  'effect_path': str(tool.path.resolve())}
        self.terminal_verifier = terminal_verifier
        self.fault = fault or (lambda _: None)
        with self._transaction(validate=False) as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            expected = self._database_bindings
            existing = dict(db.execute('SELECT key,value FROM meta'))
            if existing and existing != expected: raise Conflict('store_binding_changed')
            if not existing: db.executemany('INSERT INTO meta VALUES (?,?)', expected.items())
            db.execute('CREATE TABLE IF NOT EXISTS steps ('
                       'operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL, '
                       'payload TEXT NOT NULL, step_sha256 TEXT NOT NULL, state TEXT NOT NULL '
                       "CHECK(state IN ('allocated','requesting','proposed','abstained','invalid',"
                       "'generation_failed','applied','rejected')), "
                       'request_sha256 TEXT UNIQUE, policy_sha256 TEXT, terminal_sha256 TEXT, '
                       'proposal_json TEXT, effect_sha256 TEXT, reason TEXT, UNIQUE(task_id,step_id))')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_step ON steps(task_id) "
                       "WHERE state IN ('allocated','requesting','proposed')")
            db.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, operation_id TEXT, '
                       'snapshot_sha256 TEXT, previous_sha256 TEXT, event_sha256 TEXT)')
            self._validate(db)

    @contextmanager
    def _transaction(self, validate=True):
        if self.path.is_symlink() or self.path.resolve() != self._database_path:
            raise Conflict('host_store_path_changed')
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if validate: self._validate(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate(self, db):
        if (dict(db.execute('SELECT key,value FROM meta')) != self._database_bindings
                or str(self.journal.path.resolve()) != self._database_bindings['intent_path']
                or str(self.tool.path.resolve()) != self._database_bindings['effect_path']):
            raise Conflict('store_binding_changed')
        previous, latest = ZERO, {}
        for number, event in enumerate(db.execute('SELECT * FROM events ORDER BY sequence'), 1):
            body = {key: event[key] for key in ('sequence','operation_id','snapshot_sha256','previous_sha256')}
            if (event['sequence'] != number or event['previous_sha256'] != previous
                    or event['event_sha256'] != _sha(_json(body).encode())):
                raise Conflict('event_chain_invalid')
            previous = event['event_sha256']
            latest[event['operation_id']] = event['snapshot_sha256']
        seen = set()
        for row in db.execute('SELECT * FROM steps'):
            value = dict(row)
            step = _decode_step(row['payload'])
            if ((step.operation_id, step.task_id, step.step_id) !=
                    (row['operation_id'], row['task_id'], row['step_id'])
                    or row['step_sha256'] != _sha(row['payload'].encode())
                    or latest.get(row['operation_id']) != _sha(_json(value).encode())):
                raise Conflict('stored_step_changed')
            seen.add(row['operation_id'])
        if seen != set(latest): raise Conflict('journal_step_mismatch')

    def _audit(self, db, operation_id):
        row = dict(self._row(db, operation_id))
        prior = db.execute('SELECT sequence,event_sha256 FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        body = {'sequence': prior['sequence']+1 if prior else 1, 'operation_id': operation_id,
                'snapshot_sha256': _sha(_json(row).encode()),
                'previous_sha256': prior['event_sha256'] if prior else ZERO}
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                   (*body.values(), _sha(_json(body).encode())))

    def _row(self, db, operation_id):
        row = db.execute('SELECT * FROM steps WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None: raise Conflict('host_step_missing')
        return row

    def _no_pending_effect(self, task_id):
        if self.journal.pending(task_id) is not None:
            raise Conflict('pending_effect_requires_recovery')

    def allocate(self, step: HostStep):
        payload = _encode_step(step)
        with self._transaction() as db:
            existing = db.execute('SELECT * FROM steps WHERE operation_id=? OR (task_id=? AND step_id=?)',
                                  (step.operation_id, step.task_id, step.step_id)).fetchone()
            if existing:
                if existing['payload'] != payload: raise Conflict('host_scope_or_identity_changed')
                return dict(existing)
            self._no_pending_effect(step.task_id)
            if db.execute("SELECT 1 FROM steps WHERE task_id=? AND state IN ('allocated','requesting','proposed')",
                          (step.task_id,)).fetchone():
                raise Conflict('active_step_requires_recovery')
            db.execute("INSERT INTO steps(operation_id,task_id,step_id,payload,step_sha256,state) VALUES (?,?,?,?,?,'allocated')",
                       (step.operation_id, step.task_id, step.step_id, payload, _sha(payload.encode())))
            self._audit(db, step.operation_id)
            result = dict(self._row(db, step.operation_id))
        self.fault('after_allocation')
        return result

    def snapshot(self, operation_id):
        with self._transaction() as db: return dict(self._row(db, operation_id))

    def host_step(self, operation_id):
        return _decode_step(self.snapshot(operation_id)['payload'])

    def verify_journal(self):
        with self._transaction(): return True

    @staticmethod
    def _binding(row):
        if row['request_sha256'] is None: raise Conflict('request_not_bound')
        return RequestBinding(row['operation_id'], row['step_sha256'], row['request_sha256'], row['policy_sha256'])

    def request_binding(self, operation_id):
        """Recovery information only; this getter never authorizes another send."""
        return self._binding(self.snapshot(operation_id))

    def begin_request(self, operation_id, request_sha256, policy_sha256):
        _digest(request_sha256); _digest(policy_sha256)
        with self._transaction() as db:
            row = self._row(db, operation_id)
            if row['state'] != 'allocated': raise Conflict('request_already_bound')
            self._no_pending_effect(row['task_id'])
            if db.execute('SELECT 1 FROM steps WHERE request_sha256=?', (request_sha256,)).fetchone():
                raise Conflict('request_identity_reused')
            db.execute("UPDATE steps SET state='requesting',request_sha256=?,policy_sha256=? WHERE operation_id=?",
                       (request_sha256, policy_sha256, operation_id))
            self._audit(db, operation_id)
            binding = self._binding(self._row(db, operation_id))
        self.fault('after_request')
        return binding

    def record_terminal(self, operation_id):
        row = self.snapshot(operation_id)
        if row['state'] == 'allocated': raise Conflict('request_not_bound')
        if row['state'] != 'requesting': return row
        binding = self._binding(row)
        # Only this trusted, host-configured callback can supply terminal evidence.
        evidence = self.terminal_verifier(binding)
        if type(evidence) is not TerminalEvidence or evidence.policy_sha256 != binding.policy_sha256:
            raise Conflict('terminal_evidence_unverified')
        receipt = _loads(evidence.receipt_bytes)
        common = {'schema_version','request_sha256','server_finished','status'}
        if (type(receipt) is not dict or receipt.get('schema_version') != 'hermes-shadow-terminal/1.0'
                or receipt.get('request_sha256') != binding.request_sha256
                or receipt.get('server_finished') is not True):
            raise Conflict('terminal_binding_invalid')
        encoded_proposal = None
        if receipt.get('status') == 'failed':
            if set(receipt) != common | {'error_code'} or receipt['error_code'] not in ('budget_exhausted','runtime_error'):
                raise Conflict('terminal_shape_invalid')
            state, reason = 'generation_failed', receipt['error_code']
        elif receipt.get('status') == 'completed':
            if set(receipt) != common | {'content'} or type(receipt['content']) is not str:
                raise Conflict('terminal_shape_invalid')
            try:
                candidate = bind_tool_proposal(receipt['content'].encode('utf-8'), _decode_step(row['payload']))
            except ValueError:
                state, reason = 'invalid', 'invalid_proposal'
            else:
                encoded_proposal = receipt['content']
                state = 'proposed' if candidate.operation is not None else 'abstained'
                reason = candidate.reason
        else:
            raise Conflict('terminal_shape_invalid')
        terminal_sha = _sha(evidence.receipt_bytes)
        with self._transaction() as db:
            current = self._row(db, operation_id)
            if self._binding(current) != binding: raise Conflict('request_binding_changed')
            if current['state'] != 'requesting':
                if current['terminal_sha256'] != terminal_sha: raise Conflict('terminal_evidence_changed')
                return dict(current)
            self._no_pending_effect(current['task_id'])
            db.execute('UPDATE steps SET state=?,terminal_sha256=?,proposal_json=?,reason=? WHERE operation_id=?',
                       (state, terminal_sha, encoded_proposal, reason, operation_id))
            self._audit(db, operation_id)
            result = dict(self._row(db, operation_id))
        self.fault('after_proposal')
        return result

    def bound_proposal(self, operation_id):
        row = self.snapshot(operation_id)
        if row['proposal_json'] is None: raise Conflict('proposal_unavailable')
        return bind_tool_proposal(row['proposal_json'].encode('utf-8'), _decode_step(row['payload']))

    def reconcile_effect(self, operation_id):
        """Observe an already completed intent and matching tool receipt; no retry."""
        with self._transaction() as db:
            row = self._row(db, operation_id)
            if row['state'] in ('applied','rejected'): return dict(row)
            if row['state'] != 'proposed': raise Conflict('operation_proposal_required')
            self._no_pending_effect(row['task_id'])
            op = bind_tool_proposal(row['proposal_json'].encode(), _decode_step(row['payload'])).operation
            with self.journal.transaction() as journal_db:
                intent = journal_db.execute('SELECT * FROM intents WHERE id=?', (operation_id,)).fetchone()
            if (intent is None or intent['state'] != 'complete' or intent['payload'] != op.payload
                    or intent['task'] != op.task_id or intent['step'] != op.step_id or intent['receipt'] is None):
                raise Conflict('completed_intent_required')
            receipt = _loads(intent['receipt'].encode())
            fields = {'operation_id','request_sha256','status','code','available','version'}
            codes = ('applied','not_authorized','unknown_product','version_mismatch','quota_exceeded',
                     'insufficient_stock','reservation_mismatch','reservation_inactive')
            if (type(receipt) is not dict or set(receipt) != fields
                    or receipt['operation_id'] != op.operation_id or receipt['request_sha256'] != op.digest
                    or receipt['status'] not in ('applied','rejected') or receipt['code'] not in codes
                    or (receipt['status']=='applied') != (receipt['code']=='applied')):
                raise Conflict('effect_receipt_invalid')
            unknown = receipt['code'] in ('not_authorized','unknown_product')
            for field in ('available','version'):
                value = receipt[field]
                if value is None and unknown: continue
                if type(value) is not int or value < 0: raise Conflict('effect_counts_invalid')
            if (receipt['available'] is None) != (receipt['version'] is None):
                raise Conflict('effect_counts_invalid')
            actual = self.tool.lookup(operation_id)
            if type(actual) is not dict or _json(actual) != _json(receipt):
                raise Conflict('tool_receipt_unverified')
            db.execute('UPDATE steps SET state=?,effect_sha256=?,reason=? WHERE operation_id=?',
                       (receipt['status'], _sha(_json(receipt).encode()), receipt['code'], operation_id))
            self._audit(db, operation_id)
            result = dict(self._row(db, operation_id))
        self.fault('after_effect_record')
        return result
