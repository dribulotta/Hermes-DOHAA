"""Synthetic stock tool and separate durable intent journal; no live integrations.

Operation identities and grants belong to the host. Effects and receipts are
atomic only within this simulator's SQLite database. The executor reconciles a
pending operation before accepting a different proposal for that task.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable


class Conflict(ValueError):
    """An immutable identity, grant or receipt was reused inconsistently."""


class PendingIntent(Conflict):
    """A task must reconcile its previous operation first."""


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError('invalid_identifier')


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise ValueError('invalid_integer')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


@dataclass(frozen=True)
class Operation:
    operation_id: str
    task_id: str
    step_id: str
    kind: str
    product: str
    quantity: int
    expected_version: int
    reservation_id: str | None = None

    def __post_init__(self):
        for value in (self.operation_id, self.task_id, self.step_id, self.product):
            _text(value)
        _integer(self.quantity, 1)
        _integer(self.expected_version)
        if self.kind not in ('reserve', 'release'):
            raise ValueError('invalid_operation')
        if self.kind == 'release':
            _text(self.reservation_id)
        elif self.reservation_id is not None:
            raise ValueError('reserve_has_no_prior_reservation')

    @property
    def payload(self):
        return _json(asdict(self))

    @property
    def digest(self):
        return hashlib.sha256(self.payload.encode('utf-8')).hexdigest()


class _Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


class ToolStore(_Store):
    """Host-owned, synthetic inventory. No shell, network or external effects."""
    def __init__(self, path: Path, fault: Callable[[str], None] | None = None):
        super().__init__(path)
        self.fault = fault or (lambda _: None)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS inventory (product TEXT PRIMARY KEY, '
                       'available INTEGER NOT NULL CHECK(available>=0), version INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS grants (task TEXT, product TEXT, '
                       'quantity INTEGER NOT NULL, PRIMARY KEY(task,product))')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, task TEXT, '
                       'step TEXT, payload TEXT NOT NULL, receipt TEXT NOT NULL, UNIQUE(task,step))')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, task TEXT, '
                       'product TEXT, quantity INTEGER NOT NULL, active INTEGER NOT NULL)')

    def seed(self, product: str, quantity: int):
        _text(product)
        _integer(quantity)
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM inventory WHERE product=?', (product,)).fetchone():
                raise Conflict('inventory_already_seeded')
            db.execute('INSERT INTO inventory VALUES (?,?,0)', (product, quantity))

    def authorize(self, task: str, product: str, quantity: int):
        """Immutable host grant limits the task's total active reservation."""
        _text(task)
        _text(product)
        _integer(quantity, 1)
        with self.transaction() as db:
            existing = db.execute('SELECT quantity FROM grants WHERE task=? AND product=?',
                                  (task, product)).fetchone()
            if existing:
                if existing['quantity'] != quantity:
                    raise Conflict('grant_changed')
                return
            db.execute('INSERT INTO grants VALUES (?,?,?)', (task, product, quantity))

    def inventory(self, product: str):
        _text(product)
        with self.transaction() as db:
            row = db.execute('SELECT available,version FROM inventory WHERE product=?', (product,)).fetchone()
            return dict(row) if row else None

    def lookup(self, operation_id: str):
        _text(operation_id)
        with self.transaction() as db:
            row = db.execute('SELECT receipt FROM receipts WHERE id=?', (operation_id,)).fetchone()
            return json.loads(row['receipt']) if row else None

    def apply(self, op: Operation):
        if not isinstance(op, Operation):
            raise ValueError('operation_required')
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM receipts WHERE id=? OR (task=? AND step=?)',
                                  (op.operation_id, op.task_id, op.step_id)).fetchone()
            if previous:
                if previous['id'] != op.operation_id or previous['payload'] != op.payload:
                    raise Conflict('operation_identity_reused')
                return json.loads(previous['receipt'])
            stock = db.execute('SELECT * FROM inventory WHERE product=?', (op.product,)).fetchone()
            grant = db.execute('SELECT quantity FROM grants WHERE task=? AND product=?',
                               (op.task_id, op.product)).fetchone()
            code = 'applied'
            if not grant:
                code = 'not_authorized'
            elif stock is None:
                code = 'unknown_product'
            elif stock['version'] != op.expected_version:
                code = 'version_mismatch'
            elif op.kind == 'reserve':
                active = db.execute('SELECT COALESCE(SUM(quantity),0) FROM reservations '
                                    'WHERE task=? AND product=? AND active=1', (op.task_id, op.product)).fetchone()[0]
                if active + op.quantity > grant['quantity']:
                    code = 'quota_exceeded'
                elif stock['available'] < op.quantity:
                    code = 'insufficient_stock'
            else:
                reservation = db.execute('SELECT * FROM reservations WHERE id=?', (op.reservation_id,)).fetchone()
                if (not reservation or reservation['task'] != op.task_id
                        or reservation['product'] != op.product or reservation['quantity'] != op.quantity):
                    code = 'reservation_mismatch'
                elif not reservation['active']:
                    code = 'reservation_inactive'
            available = stock['available'] if stock else None
            version = stock['version'] if stock else None
            if code == 'applied':
                if op.kind == 'reserve':
                    available -= op.quantity
                    db.execute('INSERT INTO reservations VALUES (?,?,?,?,1)',
                               (op.operation_id, op.task_id, op.product, op.quantity))
                else:
                    available += op.quantity
                    db.execute('UPDATE reservations SET active=0 WHERE id=?', (op.reservation_id,))
                version += 1
                db.execute('UPDATE inventory SET available=?,version=? WHERE product=?',
                           (available, version, op.product))
                self.fault('after_effect_before_receipt')
            receipt = {'operation_id': op.operation_id, 'request_sha256': op.digest,
                       'status': 'applied' if code == 'applied' else 'rejected', 'code': code,
                       'available': available, 'version': version}
            db.execute('INSERT INTO receipts VALUES (?,?,?,?,?)',
                       (op.operation_id, op.task_id, op.step_id, op.payload, _json(receipt)))
            return receipt


class IntentJournal(_Store):
    """A database separate from tool effects; pending includes lost acknowledgments."""
    def __init__(self, path: Path):
        super().__init__(path)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, task TEXT, step TEXT, "
                       "payload TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','complete')), "
                       "receipt TEXT, UNIQUE(task,step))")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_task ON intents(task) WHERE state='pending'")

    def prepare(self, op: Operation):
        if not isinstance(op, Operation):
            raise ValueError('operation_required')
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM intents WHERE id=? OR (task=? AND step=?)',
                                  (op.operation_id, op.task_id, op.step_id)).fetchone()
            if previous:
                if previous['id'] != op.operation_id or previous['payload'] != op.payload:
                    raise Conflict('operation_identity_reused')
                return dict(previous)
            if db.execute("SELECT 1 FROM intents WHERE task=? AND state='pending'", (op.task_id,)).fetchone():
                raise PendingIntent('reconcile_before_new_proposal')
            db.execute("INSERT INTO intents VALUES (?,?,?,?,'pending',NULL)",
                       (op.operation_id, op.task_id, op.step_id, op.payload))
            return dict(db.execute('SELECT * FROM intents WHERE id=?', (op.operation_id,)).fetchone())

    def pending(self, task: str):
        _text(task)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE task=? AND state='pending'", (task,)).fetchone()
            return dict(row) if row else None

    def record_receipt(self, operation_id: str, receipt: dict):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM intents WHERE id=?', (operation_id,)).fetchone()
            if row is None:
                raise Conflict('intent_missing')
            op = Operation(**json.loads(row['payload']))
            expected = {'operation_id', 'request_sha256', 'status', 'code', 'available', 'version'}
            codes = ('applied', 'not_authorized', 'unknown_product', 'version_mismatch',
                     'quota_exceeded', 'insufficient_stock', 'reservation_mismatch', 'reservation_inactive')
            if (not isinstance(receipt, dict) or set(receipt) != expected
                    or receipt['operation_id'] != operation_id or receipt['request_sha256'] != op.digest
                    or receipt['status'] not in ('applied', 'rejected')
                    or receipt['code'] not in codes
                    or (receipt['status'] == 'applied') != (receipt['code'] == 'applied')):
                raise Conflict('receipt_binding_invalid')
            unknown_stock = receipt['code'] in ('not_authorized', 'unknown_product')
            for field in ('available', 'version'):
                value = receipt[field]
                if value is None and unknown_stock:
                    continue
                if type(value) is not int or value < 0:
                    raise Conflict('receipt_counts_invalid')
            if (receipt['available'] is None) != (receipt['version'] is None):
                raise Conflict('receipt_counts_invalid')
            encoded = _json(receipt)
            if row['receipt'] is not None and row['receipt'] != encoded:
                raise Conflict('receipt_changed')
            db.execute('UPDATE intents SET receipt=? WHERE id=?', (encoded, operation_id))

    def complete(self, operation_id: str):
        with self.transaction() as db:
            row = db.execute('SELECT receipt FROM intents WHERE id=?', (operation_id,)).fetchone()
            if row is None or row['receipt'] is None:
                raise PendingIntent('receipt_required')
            db.execute("UPDATE intents SET state='complete' WHERE id=?", (operation_id,))
            return json.loads(row['receipt'])


class Executor:
    """No LLM access. The host must assign the operation and stable task/step IDs."""
    def __init__(self, journal: IntentJournal, tool: ToolStore, fault=None):
        if journal.path.resolve() == tool.path.resolve():
            raise ValueError('intent_and_effect_databases_must_be_separate')
        self.journal, self.tool = journal, tool
        self.fault = fault or (lambda _: None)

    def execute(self, op: Operation):
        self.fault('before_intent')
        row = self.journal.prepare(op)
        self.fault('after_intent')
        return self._reconcile(row)

    def recover(self, task: str):
        row = self.journal.pending(task)
        return self._reconcile(row) if row else None

    def _reconcile(self, row):
        op = Operation(**json.loads(row['payload']))
        receipt = json.loads(row['receipt']) if row['receipt'] is not None else self.tool.lookup(op.operation_id)
        if receipt is None:
            # In this simulator, absent receipt implies absent committed effect.
            # Retrying this exact identity is safe even if another caller races.
            receipt = self.tool.apply(op)
        self.fault('after_effect')
        self.journal.record_receipt(op.operation_id, receipt)
        self.fault('after_receipt')
        result = self.journal.complete(op.operation_id)
        self.fault('after_complete')
        return result
