"""Actual controller admission followed by separately verified synthetic effects.

This module has no model transport or real actuator. The trusted coordinator
owns the stores; an accepted proposal is not an effect or a scored task outcome.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from hermes_dohaa.assurance.gates import ActionPolicyGate, GateResult
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.controller.identity import capture_control_plane_identity
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.learning import native_prompt as native, shadow
from hermes_dohaa.runtime.base import Proposal
from tools.controlled_tools import Conflict, Executor
from tools.host_steps import HostStepStore
from tools.native_tool_evidence import NativeToolEvidenceBridge, verifier_sha256


def _digest(value):
    return shadow._hash(shadow._canonical(value))


def route_sha256():
    return shadow._hash(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))


@dataclass(frozen=True)
class ExactVerifiedToolGate:
    contract_sha256: str
    proposal_sha256: str
    name: str = 'exact_verified_tool'

    def evaluate(self, contract, proposal):
        passed = (_digest(contract.to_dict()) == self.contract_sha256
                  and proposal.fingerprint() == self.proposal_sha256)
        return GateResult(self.name, passed, 'Exact verified host-bound proposal' if passed
                          else 'Contract or verified proposal changed',
                          failure_code=None if passed else 'result.mismatch')


class _VerifiedRuntime:
    """One read of authentic native evidence; never generates or repairs."""
    def __init__(self, workflow, operation_id, material):
        self.workflow, self.operation_id, self.material = workflow, operation_id, material
        self.used = False

    def propose(self, contract, feedback=()):
        if self.used or feedback:
            raise Conflict('one_verified_proposal_only')
        self.used = True
        material = self.workflow._material(self.operation_id)
        if material != self.material or contract.to_dict() != material['contract']:
            raise Conflict('verified_runtime_binding_changed')
        return Proposal.from_dict(material['proposal'])


class ToolWorkflow:
    def __init__(self, root, *, route, host, bridge, executor):
        if (route not in ('dohaa', 'simple') or type(host) is not HostStepStore
                or type(bridge) is not NativeToolEvidenceBridge or type(executor) is not Executor
                or host.terminal_verifier != bridge.verify or executor.tool is not host.tool
                or executor.journal is not host.journal):
            raise Conflict('trusted_tool_route_components_required')
        self.host, self.bridge, self.executor, self.route = host, bridge, executor, route
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, exist_ok=True)
        native.protected_directory(self.root)
        self.path = self.root/'decisions.db'
        self.ledger_path = self.root/'controller.db'
        self.owner_path = Path(str(host.path.absolute()) + '.tool-route')
        self.meta = dict(route=route, root=str(self.root), source=route_sha256(),
                        verifier=verifier_sha256(), host=str(host.path.absolute()),
                        intent=str(host.journal.path.absolute()), effect=str(host.tool.path.absolute()),
                        bridge=str(bridge.path.absolute()), residency=str(bridge.residency.path.absolute()))
        if tuple(self.meta[k] for k in ('host', 'intent', 'effect')) != tuple(map(str, bridge.host_paths)):
            raise Conflict('bridge_store_binding_changed')
        self.fault = lambda _: None
        self.ledger = None
        with self._exclusive():
            if self.owner_path.exists():
                if native.protected_read(self.owner_path) != shadow._canonical(self.meta):
                    raise Conflict('host_route_already_bound')
            else:
                shadow._publish(self.owner_path, shadow._canonical(self.meta))
            fresh = self._create(self.path)
            with self._transaction(initial=True) as db:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
                old = dict(db.execute('SELECT * FROM meta'))
                if old != self.meta:
                    if old or not fresh:
                        raise Conflict('route_initialization_or_metadata_changed')
                    db.executemany('INSERT INTO meta VALUES (?,?)', self.meta.items())
                db.execute('CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, payload TEXT, digest TEXT)')
            ledger_fresh = self._create(self.ledger_path)
            if not fresh and ledger_fresh:
                raise Conflict('route_ledger_missing')
            self.ledger = EvidenceLedger(self.ledger_path, create=fresh)
            self.ledger._connection.execute('PRAGMA synchronous=FULL')
            self._protect()

    @staticmethod
    def _create(path):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            native.protected_file(path)
            return False
        os.close(descriptor)
        return True

    def close(self):
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None

    def _protect(self):
        native.protected_directory(self.root)
        native.protected_file(self.path)
        native.protected_file(self.ledger_path)
        for suffix in ('-wal', '-shm', '-journal'):
            for path in (self.path, self.ledger_path):
                companion = Path(str(path) + suffix)
                if companion.exists():
                    native.protected_file(companion)
        if (native.protected_read(self.owner_path) != shadow._canonical(self.meta)
                or self.meta['source'] != route_sha256() or self.meta['verifier'] != verifier_sha256()
                or self.route != self.meta['route'] or self.host.terminal_verifier != self.bridge.verify
                or self.executor.tool is not self.host.tool or self.executor.journal is not self.host.journal
                or str(self.bridge.path.absolute()) != self.meta['bridge']
                or str(self.host.path.absolute()) != self.meta['host']):
            raise Conflict('route_binding_or_source_changed')

    @contextmanager
    def _exclusive(self):
        import fcntl
        lock = self.root/'route.lock'
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            native.protected_file(lock)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict('tool_route_already_running') from None
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def _transaction(self, initial=False):
        native.protected_file(self.path)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if not initial and dict(db.execute('SELECT * FROM meta')) != self.meta:
                raise Conflict('route_metadata_changed')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, operation_id):
        self._protect()
        with self._transaction() as db:
            row = db.execute('SELECT payload,digest FROM decisions WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if _digest(result) != row[1] or result['operation_id'] != operation_id:
            raise Conflict('route_decision_changed')
        return result

    def _write(self, row):
        with self._transaction() as db:
            db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?)',
                       (row['operation_id'], shadow._canonical(row).decode(), _digest(row)))

    def _pending(self, operation_id, allow_original=False):
        step = self.host.host_step(operation_id)
        pending = self.host.journal.pending(step.task_id)
        if pending is not None and (not allow_original or pending['id'] != operation_id):
            raise Conflict('pending_effect_requires_recovery')

    def _no_effect(self, operation_id):
        with self.host.journal.transaction() as db:
            intent = db.execute('SELECT 1 FROM intents WHERE id=?', (operation_id,)).fetchone()
        if intent is not None or self.host.tool.lookup(operation_id) is not None:
            raise Conflict('effect_without_route_execution_intent')

    def _material(self, operation_id):
        row = self.host.snapshot(operation_id)
        binding = self.host.request_binding(operation_id)
        evidence = self.bridge.verify(binding)
        if shadow._hash(evidence.receipt_bytes) != row['terminal_sha256']:
            raise Conflict('native_terminal_changed')
        core = {key: row[key] for key in ('operation_id', 'payload', 'step_sha256',
                                         'request_sha256', 'policy_sha256', 'terminal_sha256', 'proposal_json')}
        if row['proposal_json'] is None:
            if row['state'] not in ('invalid', 'generation_failed'):
                raise Conflict('verified_proposal_unavailable')
            return dict(core=core, unavailable=row['state'], reason=row['reason'])
        step = self.host.host_step(operation_id)
        bound = self.host.bound_proposal(operation_id)
        proposal = Proposal(result=dict(tool_proposal=json.loads(row['proposal_json']),
                                        verified_binding=asdict(binding), terminal_sha256=row['terminal_sha256']),
                            requested_actions=(() if bound.operation is None else
                                               ('simulator.' + bound.operation.kind,)))
        contract = TaskContract.from_dict(dict(
            contract_id='tool-' + _digest(core), objective='Admit one exact verified synthetic tool proposal',
            acceptance_criteria=[dict(criterion_id='verified', description='Exact host and native evidence binding')],
            inputs=dict(host_step=json.loads(row['payload']), verified_binding=asdict(binding)),
            allowed_actions=sorted('simulator.' + kind for kind in step.allowed_kinds),
            max_attempts=1, requires_human_approval=False, risk_level='low'))
        gates = self._gates(contract, proposal)
        return dict(core=core, contract=contract.to_dict(), proposal=proposal.to_dict(),
                    control_plane=capture_control_plane_identity(gates).sha256,
                    abstained=bound.operation is None)

    @staticmethod
    def _gates(contract, proposal):
        return (ExactVerifiedToolGate(_digest(contract.to_dict()), proposal.fingerprint()), ActionPolicyGate())

    def _simple(self, runtime, contract, gates):
        run_id = str(uuid4())
        self.ledger.append(run_id, 'simple.received', dict(contract=contract.to_dict()))
        proposal = runtime.propose(contract)
        self.ledger.append(run_id, 'simple.proposal', dict(attempt=1, source='proposal', fingerprint=proposal.fingerprint()))
        results = [gate.evaluate(contract, proposal).to_dict() for gate in gates]
        self.ledger.append(run_id, 'simple.gates', dict(attempt=1, results=results))
        status = 'succeeded' if all(r['passed'] for r in results) else 'escalated'
        self.ledger.append(run_id, 'simple.finished', dict(attempts=1, status=status))
        return run_id

    def _proof(self, material, result=None):
        self.ledger.verify_chain()
        names = ('run.received', 'proposal.received', 'gates.evaluated', 'run.finished') if self.route == 'dohaa' else (
            'simple.received', 'simple.proposal', 'simple.gates', 'simple.finished')
        matches = [r for r in self.ledger.records() if r.event_type == names[0]
                   and r.payload.get('contract') == material['contract']]
        if len(matches) != 1:
            raise Conflict('unique_completed_decision_required')
        run_id = matches[0].run_id
        records = list(self.ledger.records(run_id))
        groups = [[r for r in records if r.event_type == name] for name in names]
        if (any(len(group) != 1 for group in groups) or records[0] != groups[0][0]
                or records[-1] != groups[-1][0]
                or any(r.event_type not in (*names, 'state.changed', 'retry.scheduled') for r in records)):
            raise Conflict('single_unrepaired_decision_required')
        proposal, gates, finished = (group[0].payload for group in groups[1:])
        if (proposal.get('attempt') != 1 or proposal.get('source') != 'proposal'
                or gates.get('attempt') != 1 or 'source' in gates or finished.get('attempts') != 1):
            raise Conflict('single_unrepaired_decision_required')
        gate_results = [GateResult.from_dict(r) for r in gates['results']]
        if [g.gate for g in gate_results] != ['exact_verified_tool', 'action_policy']:
            raise Conflict('fixed_tool_gates_required')
        accepted = all(g.passed for g in gate_results)
        if finished['status'] != ('succeeded' if accepted else 'escalated'):
            raise Conflict('decision_gate_status_mismatch')
        expected = Proposal.from_dict(material['proposal'])
        if accepted:
            contract = TaskContract.from_dict(material['contract'])
            if (proposal['fingerprint'] != expected.fingerprint() or gates['results'] !=
                    [g.evaluate(contract, expected).to_dict() for g in self._gates(contract, expected)]):
                raise Conflict('accepted_proposal_not_verified')
        if result is not None:
            if (result.run_id != run_id or result.status != finished['status'] or result.attempts != 1
                    or result.proposal is None or result.proposal.fingerprint() != proposal['fingerprint']
                    or [g.to_dict() for g in result.gate_results] != gates['results']):
                raise Conflict('controller_result_not_ledger_decision')
        return dict(run_id=run_id, ledger_sha256=_digest([asdict(r) for r in records]),
                    admission='accepted' if accepted else 'denied', status=finished['status'])

    def _check(self, row):
        if self._material(row['operation_id']) != row['material']:
            raise Conflict('route_verified_material_changed')
        if row['proof'] is not None and self._proof(row['material']) != row['proof']:
            raise Conflict('recorded_controller_decision_changed')

    def decide(self, operation_id):
        with self._exclusive():
            old = self._read(operation_id)
            if old is not None:
                return self._snapshot(old)
            self._pending(operation_id)
            self._no_effect(operation_id)
            self.host.record_terminal(operation_id)
            material = self._material(operation_id)
            row = dict(operation_id=operation_id, state='deciding', material=material, proof=None, effect_sha256=None)
            self._write(row)
            self.fault('decision_intent')
            if 'unavailable' in material:
                row['state'] = material['unavailable']
                self._write(row)
                return self._snapshot(row)
            contract, proposal = TaskContract.from_dict(material['contract']), Proposal.from_dict(material['proposal'])
            runtime = _VerifiedRuntime(self, operation_id, material)
            gates = self._gates(contract, proposal)
            if self.route == 'dohaa':
                result = DohaaController(runtime=runtime, gates=gates, ledger=self.ledger).run(contract)
                proof = self._proof(material, result)
            else:
                run_id = self._simple(runtime, contract, gates)
                proof = self._proof(material)
                if proof['run_id'] != run_id:
                    raise Conflict('simple_decision_binding_changed')
            self.fault('controller_finished')
            self._commit_decision(row, proof)
            return self._snapshot(row)

    def _commit_decision(self, row, proof):
        row['proof'] = proof
        row['state'] = ('abstained' if row['material']['abstained'] else 'accepted') if proof['admission'] == 'accepted' else 'denied'
        self._write(row)
        self.fault('decision_committed')

    def _receipt(self, row):
        operation_id = row['operation_id']
        op = self.host.bound_proposal(operation_id).operation
        host_row = self.host.snapshot(operation_id)
        with self.host.journal.transaction() as db:
            intent = db.execute('SELECT * FROM intents WHERE id=?', (operation_id,)).fetchone()
        if (op is None or intent is None or intent['state'] != 'complete' or intent['payload'] != op.payload
                or intent['task'] != op.task_id or intent['step'] != op.step_id or intent['receipt'] is None):
            raise Conflict('original_completed_intent_required')
        receipt = json.loads(intent['receipt'])
        if (self.host.tool.lookup(operation_id) != receipt or receipt.get('request_sha256') != op.digest
                or receipt.get('operation_id') != operation_id or _digest(receipt) != host_row['effect_sha256']
                or host_row['state'] != receipt.get('status') or host_row['reason'] != receipt.get('code')
                or host_row['state'] not in ('applied', 'rejected')):
            raise Conflict('original_effect_receipt_required')
        if row['effect_sha256'] is not None and row['effect_sha256'] != _digest(receipt):
            raise Conflict('recorded_effect_changed')
        return receipt

    def _execute(self, row):
        self._check(row)
        self._pending(row['operation_id'], allow_original=row['state'] == 'executing')
        if row['state'] == 'accepted':
            self._no_effect(row['operation_id'])
            row['state'] = 'executing'
            self._write(row)
            self.fault('execution_intent')
        if row['state'] == 'executing':
            if row['proof'] is None or row['proof']['admission'] != 'accepted' or row['material']['abstained']:
                raise Conflict('accepted_effect_proposal_required')
            # Original identity only; this simulator atomically commits effect and receipt.
            op = self.host.bound_proposal(row['operation_id']).operation
            self.executor.execute(op)
            self.host.reconcile_effect(row['operation_id'])
            receipt = self._receipt(row)
            self.fault('effect_observed')
            row.update(state=receipt['status'], effect_sha256=_digest(receipt))
            self._write(row)
        return self._snapshot(row)

    def execute(self, operation_id):
        with self._exclusive():
            row = self._read(operation_id)
            if row is None or row['state'] == 'deciding':
                raise Conflict('durable_decision_required')
            return self._execute(row)

    def recover(self, operation_id):
        with self._exclusive():
            row = self._read(operation_id)
            if row is None:
                raise Conflict('durable_decision_required')
            self._check(row)
            if row['state'] == 'deciding':
                self._pending(operation_id)
                self._no_effect(operation_id)
                if 'unavailable' in row['material']:
                    row['state'] = row['material']['unavailable']
                    self._write(row)
                else:
                    # No controller/model replay when its completion is missing.
                    self._commit_decision(row, self._proof(row['material']))
            if row['state'] == 'executing':
                return self._execute(row)
            return self._snapshot(row)

    def _snapshot(self, row):
        self._check(row)
        if row['state'] in ('deciding', 'executing'):
            raise Conflict('route_recovery_required')
        receipt = None
        if row['state'] in ('applied', 'rejected'):
            receipt = self._receipt(row)
            if receipt['status'] != row['state']:
                raise Conflict('route_effect_status_changed')
        else:
            self._pending(row['operation_id'])
            self._no_effect(row['operation_id'])
        return dict(operation_id=row['operation_id'], route=self.route, state=row['state'],
                    admission=row['proof']['admission'] if row['proof'] else None,
                    controller_status=row['proof']['status'] if self.route == 'dohaa' and row['proof'] else None,
                    effect_applied=receipt is not None and receipt['status'] == 'applied',
                    effect_code=receipt['code'] if receipt else None)

    def snapshot(self, operation_id):
        with self._exclusive():
            row = self._read(operation_id)
            if row is None:
                raise Conflict('durable_decision_required')
            return self._snapshot(row)
