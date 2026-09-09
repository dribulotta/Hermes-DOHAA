"""Trusted host coordinator for one fixed native proposal, with durable recovery.

No model output chooses a scope, evidence path, model identity, or executor.
Only the native adapter can publish terminal artifacts in its private directory.
All host stores and the residency journal must remain inaccessible to workers.
This is Linux development infrastructure, not remote cryptographic attestation.
"""
from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import sqlite3

from hermes_dohaa.learning import collection, native_prompt as native, shadow
from hermes_dohaa.learning.native_tool_contract import TOOL_POLICY_VERSION
from tools.controlled_tools import Conflict
from tools.host_steps import HostStepStore, RequestBinding, TerminalEvidence
from tools.native_model_residency import ModelResidency


def verifier_sha256():
    root = Path(__file__).parent
    return shadow._hash(shadow._canonical({name: shadow._hash((root/name).read_bytes().replace(b'\r\n', b'\n'))
        for name in ('native_tool_evidence.py', 'native_model_residency.py', 'host_steps.py',
                     'controlled_tool_proposal.py', 'controlled_tools.py')}))


class NativeToolEvidenceBridge:
    def __init__(self, root, *, policy_bytes, collection_policy_bytes, residency,
                 native_evidence_dir, host_path, intent_path, effect_path):
        self.policy_bytes = bytes(policy_bytes)
        self.policy_sha = shadow._hash(self.policy_bytes)
        self.policy = native.validate_native_policy(self.policy_bytes, self.policy_sha)
        if self.policy['schema_version'] != TOOL_POLICY_VERSION:
            raise Conflict('fixed_native_tool_policy_required')
        self.collection_sha = shadow._hash(collection_policy_bytes)
        cp = shadow._json(collection_policy_bytes, self.collection_sha)
        expected = collection.create_collection_policy(adapter_sha256=native.native_adapter_sha256(),
            runtime_policy_sha256=self.policy_sha, result_fields=cp.get('result_fields'))
        if not shadow._equal(cp, expected): raise Conflict('collection_policy_changed')
        if type(residency) is not ModelResidency: raise Conflict('durable_residency_required')
        self.residency = residency
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, exist_ok=True)
        native.protected_directory(self.root)
        self.path = self.root/'requests.db'
        self.evidence_dir = Path(native_evidence_dir).absolute()
        self.host_paths = tuple(Path(p).absolute() for p in (host_path, intent_path, effect_path))
        self.residency_path = residency.path.absolute()
        if len({self.path, self.residency_path, *self.host_paths}) != 5:
            raise Conflict('separate_databases_required')
        self.meta = {'schema': 'hermes-native-tool-bridge/1.0', 'policy': self.policy_sha,
                     'collection': self.collection_sha, 'verifier': verifier_sha256(),
                     'evidence': str(self.evidence_dir), 'residency': str(self.residency_path),
                     **dict(zip(('host', 'intent', 'effect'), map(str, self.host_paths)))}
        descriptor = None
        try: descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError: pass
        finally:
            if descriptor is not None: os.close(descriptor)
        with self._transaction(initial=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
            found = dict(db.execute('SELECT * FROM meta'))
            if found and found != self.meta: raise Conflict('bridge_metadata_changed')
            if not found: db.executemany('INSERT INTO meta VALUES (?,?)', self.meta.items())
            db.execute('CREATE TABLE IF NOT EXISTS requests (request_sha TEXT PRIMARY KEY, '
                       'operation_id TEXT UNIQUE, binding TEXT, request BLOB, ownership TEXT, proof_sha TEXT)')

    def _protect(self, *, initial=False):
        native.protected_directory(self.root)
        native.protected_file(self.path)
        native.protected_directory(self.evidence_dir)
        for path in (*self.host_paths, self.residency_path):
            native.protected_directory(path.parent)
            if path.exists() or not initial: native.protected_file(path)
        if (self.residency.path.absolute() != self.residency_path
                or self.residency.model != self.policy['model']
                or self.residency.context != self.policy['context_length']
                or self.residency.policy_sha != self.policy_sha
                or native.validate_native_policy(self.policy_bytes, self.policy_sha) != self.policy
                or verifier_sha256() != self.meta['verifier']):
            raise Conflict('bridge_configuration_changed')

    @contextmanager
    def _transaction(self, initial=False):
        self._protect(initial=initial)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            if not initial and dict(db.execute('SELECT * FROM meta')) != self.meta:
                raise Conflict('bridge_metadata_changed')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()

    def generate(self, host, operation_id, request_bytes, adapter):
        """Persist before dispatch. No repeat invocation is a valid resend lease."""
        self._protect()
        if (type(host) is not HostStepStore
                or (host.path.absolute(), host.journal.path.absolute(), host.tool.path.absolute()) != self.host_paths
                or host.terminal_verifier != self.verify
                or type(adapter) is not native.NativePromptAdapter
                or adapter.runtime_policy_sha256 != self.policy_sha
                or adapter.collection_sha256 != self.collection_sha
                or adapter.evidence_dir.absolute() != self.evidence_dir
                or dict(adapter.policy) != self.policy):
            raise Conflict('trusted_coordinator_binding_required')
        native.validate_request(request_bytes, self.collection_sha)
        request_sha = shadow._hash(request_bytes)
        row = self.residency.snapshot()
        if row['state'] != 'ready': raise Conflict('model_completion_unresolved')
        self.residency._same_instance(row)
        if (adapter.state != 'idle' or adapter.owned not in (set(), {row['instance_id']})
                or adapter._catalog() != {row['instance_id']: self.policy['model']}
                or not row['load_receipt_sha']):
            raise Conflict('native_residency_unbound')
        native.verified_tool_session(self.evidence_dir, self.collection_sha, self.policy, self.policy_sha)
        adapter.owned = {row['instance_id']}
        binding = host.begin_request(operation_id, request_sha, self.policy_sha)
        ownership = {'instance_id': row['instance_id'], 'load_receipt_sha256': row['load_receipt_sha'],
                     'context_length': row['observed_context'], 'model': row['model']}
        with self._transaction() as db:
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL)',
                (request_sha, operation_id, shadow._canonical(asdict(binding)).decode(), request_bytes,
                 shadow._canonical(ownership).decode()))
        # A crash on either side of these durable boundaries leaves the request
        # unresolved. Recovery only reads artifacts; it never dispatches again.
        self.residency.begin_generation(request_sha)
        returned = adapter.generate(request_bytes)
        receipt, _ = self._native(binding)
        if returned != receipt: raise Conflict('returned_terminal_changed')
        self.residency.record_terminal(request_sha, receipt)
        return self.verify(binding)

    def _native(self, binding):
        if type(binding) is not RequestBinding: raise Conflict('host_binding_required')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM requests WHERE request_sha=?', (binding.request_sha256,)).fetchone()
            if (row is None or row['binding'] != shadow._canonical(asdict(binding)).decode()
                    or binding.policy_sha256 != self.policy_sha
                    or shadow._hash(row['request']) != binding.request_sha256):
                raise Conflict('stored_host_request_changed')
            row = dict(row)
        receipt, proof_sha = native.verified_tool_terminal(self.evidence_dir, row['request'],
                                                         self.collection_sha, self.policy, self.policy_sha)
        with self._transaction() as db:
            current = db.execute('SELECT proof_sha FROM requests WHERE request_sha=?',
                                 (binding.request_sha256,)).fetchone()[0]
            if current is not None and current != proof_sha: raise Conflict('terminal_artifact_changed')
            db.execute('UPDATE requests SET proof_sha=? WHERE request_sha=?', (proof_sha, binding.request_sha256))
        ownership = shadow._json(row['ownership'].encode(), shadow._hash(row['ownership'].encode()))
        return receipt, ownership

    def verify(self, binding):
        """HostStepStore callback; accepts no caller receipt or evidence path."""
        receipt, ownership = self._native(binding)
        terminal = self.residency.terminal_evidence(binding.request_sha256)
        if (terminal['terminal_sha256'] != shadow._hash(receipt)
                or ownership != {key: terminal[key] for key in
                                 ('instance_id','load_receipt_sha256','context_length','model')}):
            raise Conflict('native_ownership_evidence_mismatch')
        return TerminalEvidence(self.policy_sha, receipt)

    def recover(self, binding):
        """Complete ownership bookkeeping only from verified native artifacts."""
        receipt, ownership = self._native(binding)
        row = self.residency.snapshot()
        if row['state'] == 'generating':
            if (row['request_sha'] != binding.request_sha256
                    or ownership != {'instance_id': row['instance_id'], 'load_receipt_sha256': row['load_receipt_sha'],
                                     'context_length': row['observed_context'], 'model': row['model']}):
                raise Conflict('generation_ownership_changed')
            self.residency.record_terminal(binding.request_sha256, receipt)
        return self.verify(binding)
