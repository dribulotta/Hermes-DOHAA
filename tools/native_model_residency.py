"""Durable ownership for a single exclusive development model instance.

Transport callbacks belong to the trusted collector. This module never guesses
ownership from a model name, reissues an uncertain operation, or treats worker
progress/diagnostics as a terminal generation receipt.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


class ResidencyError(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ResidencyError('invalid_digest')


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ResidencyError('invalid_identifier')


class ModelResidency:
    def __init__(self, path: Path, *, model, context_length, policy_sha256,
                 catalog, load, unload, fault=None):
        _identifier(model)
        _hash(policy_sha256)
        if type(context_length) is not int or not 1 <= context_length <= 262144:
            raise ResidencyError('invalid_context')
        self.path = Path(path)
        if self.path.is_symlink():
            raise ResidencyError('journal_symlink')
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.model, self.context = model, context_length
        self.policy_sha = policy_sha256
        self.catalog, self.load, self.unload = catalog, load, unload
        self.fault = fault or (lambda _: None)
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS residency (singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
                       'model TEXT, context INTEGER, policy_sha TEXT, state TEXT, instance_id TEXT, '
                       'observed_context INTEGER, request_sha TEXT, load_receipt_sha TEXT, terminal_sha TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, state TEXT, details TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS terminals (request_sha TEXT PRIMARY KEY, payload TEXT, sha TEXT)')
            db.execute("INSERT OR IGNORE INTO residency VALUES (1,?,?,?,'new',NULL,NULL,NULL,NULL,NULL)",
                       (model, context_length, policy_sha256))
            row = db.execute('SELECT * FROM residency').fetchone()
            if (row['model'], row['context'], row['policy_sha']) != (model, context_length, policy_sha256):
                raise ResidencyError('journal_binding_changed')

    @contextmanager
    def _transaction(self):
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

    def snapshot(self):
        with self._transaction() as db:
            return dict(db.execute('SELECT * FROM residency').fetchone())

    def _transition(self, expected, state, *, require_request=None, **fields):
        allowed = {'instance_id', 'observed_context', 'request_sha', 'load_receipt_sha', 'terminal_sha'}
        if not set(fields) <= allowed:
            raise ResidencyError('unknown_journal_field')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM residency').fetchone()
            if row['state'] not in expected:
                raise ResidencyError('operation_unresolved_or_not_ready')
            if require_request is not None and row['request_sha'] != require_request:
                raise ResidencyError('terminal_request_changed')
            if state == 'generating' and db.execute('SELECT 1 FROM terminals WHERE request_sha=?',
                                                   (fields['request_sha'],)).fetchone():
                raise ResidencyError('request_already_completed')
            if require_request is not None and state == 'ready':
                terminal = {'request_sha256': require_request, 'terminal_sha256': fields['terminal_sha'],
                            'policy_sha256': row['policy_sha'], 'model': row['model'],
                            'context_length': row['observed_context'], 'instance_id': row['instance_id'],
                            'load_receipt_sha256': row['load_receipt_sha']}
                payload = _canonical(terminal)
                db.execute('INSERT INTO terminals VALUES (?,?,?)',
                           (require_request, payload.decode(), _digest(payload)))
            assignments = ','.join(['state=?'] + [name + '=?' for name in fields])
            db.execute('UPDATE residency SET ' + assignments + ' WHERE singleton=1', (state, *fields.values()))
            db.execute('INSERT INTO events(state,details) VALUES (?,?)',
                       (state, _canonical(fields).decode()))

    def _catalog(self):
        raw = self.catalog()
        if type(raw) is not dict or type(raw.get('models')) is not list:
            raise ResidencyError('invalid_catalog')
        keys, instances = set(), {}
        for model in raw['models']:
            if type(model) is not dict:
                raise ResidencyError('invalid_catalog')
            key = model.get('key')
            _identifier(key)
            if key in keys:
                raise ResidencyError('duplicate_model')
            keys.add(key)
            if key == self.model:
                maximum = model.get('max_context_length')
                if type(maximum) is not int or maximum < self.context:
                    raise ResidencyError('context_capacity_unverified')
            loaded = model.get('loaded_instances')
            if type(loaded) is not list:
                raise ResidencyError('invalid_instances')
            for instance in loaded:
                if type(instance) is not dict:
                    raise ResidencyError('invalid_instance')
                identifier = instance.get('id')
                _identifier(identifier)
                if identifier in instances:
                    raise ResidencyError('duplicate_instance')
                config = instance.get('config')
                context = config.get('context_length') if type(config) is dict else None
                if type(context) is not int or context < 1:
                    raise ResidencyError('instance_context_unverified')
                instances[identifier] = (key, context)
        if self.model not in keys:
            raise ResidencyError('model_unavailable')
        return instances

    def _same_instance(self, row):
        if self._catalog() != {row['instance_id']: (self.model, row['observed_context'])}:
            raise ResidencyError('residency_changed')

    def start(self):
        if self.snapshot()['state'] != 'new':
            raise ResidencyError('load_already_attempted')
        if self._catalog():
            raise ResidencyError('backend_not_empty')
        self._transition({'new'}, 'loading')
        self.fault('load_intent_committed')
        response = self.load({'model': self.model, 'context_length': self.context, 'echo_load_config': True})
        if type(response) is not dict or response.get('status') != 'loaded':
            raise ResidencyError('load_terminal_unverified')
        identifier = response.get('instance_id')
        _identifier(identifier)
        config = response.get('load_config')
        observed = config.get('context_length') if type(config) is dict else None
        if type(observed) is not int or not 1 <= observed <= 262144:
            observed = None
        receipt = {'instance_id': identifier, 'context_length': observed, 'policy_sha256': self.policy_sha}
        state = 'ready' if observed == self.context else 'loaded_invalid'
        self._transition({'loading'}, state, instance_id=identifier, observed_context=observed,
                         load_receipt_sha=_digest(_canonical(receipt)))
        self.fault('load_receipt_committed')
        if state != 'ready':
            raise ResidencyError('load_context_mismatch')
        self._same_instance(self.snapshot())

    def begin_generation(self, request_sha256):
        _hash(request_sha256)
        row = self.snapshot()
        if row['state'] != 'ready':
            raise ResidencyError('generation_not_ready')
        self._same_instance(row)
        self._transition({'ready'}, 'generating', request_sha=request_sha256, terminal_sha=None)
        self.fault('generation_intent_committed')

    def record_terminal(self, request_sha256, receipt_bytes):
        _hash(request_sha256)
        from hermes_dohaa.learning import shadow
        try:
            receipt = shadow._json(receipt_bytes, _digest(receipt_bytes))
        except Exception:
            raise ResidencyError('terminal_receipt_invalid') from None
        row = self.snapshot()
        if (row['state'] != 'generating' or row['request_sha'] != request_sha256
                or receipt.get('request_sha256') != request_sha256
                or receipt.get('schema_version') != 'hermes-shadow-terminal/1.0'
                or receipt.get('server_finished') is not True
                or receipt.get('status') not in ('completed', 'failed')):
            raise ResidencyError('terminal_binding_unverified')
        fields = {'schema_version', 'request_sha256', 'server_finished', 'status'}
        if receipt['status'] == 'completed':
            valid = set(receipt) == fields | {'content'} and isinstance(receipt.get('content'), str)
        else:
            valid = (set(receipt) == fields | {'error_code'}
                     and receipt.get('error_code') in ('budget_exhausted', 'runtime_error'))
        if not valid:
            raise ResidencyError('terminal_shape_invalid')
        self._transition({'generating'}, 'ready', require_request=request_sha256,
                         request_sha=None, terminal_sha=_digest(receipt_bytes))

    def terminal_evidence(self, request_sha256):
        """Durable ownership binding, not a substitute for verified native wire."""
        _hash(request_sha256)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM terminals WHERE request_sha=?', (request_sha256,)).fetchone()
        if row is None: raise ResidencyError('terminal_history_missing')
        raw = row['payload'].encode()
        from hermes_dohaa.learning import shadow
        item = shadow._json(raw, row['sha'])
        if (set(item) != {'request_sha256','terminal_sha256','policy_sha256','model',
                          'context_length','instance_id','load_receipt_sha256'}
                or item['request_sha256'] != request_sha256 or item['policy_sha256'] != self.policy_sha
                or item['model'] != self.model or type(item['context_length']) is not int
                or item['context_length'] != self.context):
            raise ResidencyError('terminal_history_binding')
        _identifier(item['instance_id']); _hash(item['terminal_sha256'])
        load = {'instance_id': item['instance_id'], 'context_length': item['context_length'],
                'policy_sha256': item['policy_sha256']}
        if item['load_receipt_sha256'] != _digest(_canonical(load)):
            raise ResidencyError('load_history_binding')
        return item

    def finish(self):
        row = self.snapshot()
        if row['state'] not in ('ready', 'loaded_invalid'):
            raise ResidencyError('completion_or_ownership_unresolved')
        self._same_instance(row)
        self._transition({'ready', 'loaded_invalid'}, 'unloading')
        self.fault('unload_intent_committed')
        self.unload({'instance_id': row['instance_id']})
        if self._catalog():
            raise ResidencyError('unload_unverified')
        self._transition({'unloading'}, 'closed')
