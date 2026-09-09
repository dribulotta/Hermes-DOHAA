"""Fresh native multi-step flows through actual controller or simple routes.

One trusted coordinator, one prospective flow, one owned model/profile block.
Reopening is recovery/finalization only. Declared fault stages exit the process
with code 86; only an external observer can attest that the exit occurred.
"""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from hermes_dohaa.learning import native_prompt as native, shadow
from tools.controlled_tools import Conflict
from tools.controlled_tool_routes import ToolWorkflow, route_sha256
from tools.host_steps import _decode_step, _encode_step
from tools.native_tool_canary import observed_state, _state


OBSERVATION_SEPARATOR = '\n\nObserved simulator state (data):\n'
TERMINAL = ('applied', 'rejected', 'abstained', 'denied', 'invalid', 'generation_failed')


def _digest(value):
    return shadow._hash(shadow._canonical(value))


def collector_sha256():
    return shadow._hash(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))


def grants_sha256(tool):
    with tool.transaction() as db:
        return _digest([dict(row) for row in db.execute('SELECT * FROM grants ORDER BY task,product')])


def _step(case):
    return _decode_step(shadow._canonical(case['host_step']).decode())


def _validate(raw, digest, workflow):
    plan = shadow._json(raw, digest)
    shadow._fields(plan, {'schema_version', 'collector_sha256', 'route_sha256', 'route',
                         'runtime_policy_sha256', 'collection_policy_sha256', 'task_id',
                         'initial_state_sha256', 'grants_sha256', 'steps'})
    if (plan['schema_version'] != 'hermes-native-multistep/1.0'
            or plan['collector_sha256'] != collector_sha256() or plan['route_sha256'] != route_sha256()
            or plan['route'] != workflow.route
            or plan['runtime_policy_sha256'] != workflow.bridge.policy_sha
            or plan['collection_policy_sha256'] != workflow.bridge.collection_sha):
        raise Conflict('multistep_source_policy_or_route_changed')
    for key in ('initial_state_sha256', 'grants_sha256'):
        shadow._digest(plan[key])
    steps = plan['steps']
    if type(steps) is not list or not 1 <= len(steps) <= min(8, workflow.bridge.policy['request_limit']):
        raise Conflict('bounded_multistep_plan_required')
    identities, requests = set(), set()
    for case in steps:
        shadow._fields(case, {'host_step', 'request_base64', 'continue_on', 'fault_stage', 'expected'})
        step = _step(case)
        if step.task_id != plan['task_id'] or any(x in identities for x in (step.step_id, step.operation_id)):
            raise Conflict('duplicate_or_cross_task_step')
        identities.update((step.step_id, step.operation_id))
        request = base64.b64decode(case['request_base64'], validate=True)
        native.validate_request(request, workflow.bridge.collection_sha)
        request_id = json.loads(request)['request_id']
        if request_id in requests:
            raise Conflict('request_identity_reused')
        requests.add(request_id)
        continuation = case['continue_on']
        if (type(continuation) is not list or len(continuation) != len(set(continuation))
                or any(value not in ('applied', 'rejected', 'abstained') for value in continuation)
                or case['fault_stage'] not in ('none', 'execution_intent', 'tool_effect')):
            raise Conflict('invalid_flow_control_or_fault')
        expected = case['expected']
        shadow._fields(expected, {'route_state', 'effect_code', 'host_reason', 'actual_state'})
        if expected['route_state'] not in TERMINAL or type(expected['host_reason']) is not str:
            raise Conflict('invalid_expected_terminal')
        if ((expected['route_state'] == 'applied' and expected['effect_code'] != 'applied')
                or (expected['route_state'] == 'rejected' and expected['effect_code'] not in (
                    'not_authorized', 'unknown_product', 'version_mismatch', 'quota_exceeded',
                    'insufficient_stock', 'reservation_mismatch', 'reservation_inactive'))
                or (expected['route_state'] not in ('applied', 'rejected') and expected['effect_code'] is not None)):
            raise Conflict('invalid_expected_effect_code')
        _state(expected['actual_state'])
    return plan


class MultistepCollector:
    def __init__(self, root, *, plan_bytes, expected_sha256, workflow, adapter):
        if (type(workflow) is not ToolWorkflow or type(adapter) is not native.NativePromptAdapter
                or adapter.runtime_policy_sha256 != workflow.bridge.policy_sha
                or adapter.collection_sha256 != workflow.bridge.collection_sha
                or adapter.evidence_dir.absolute() != workflow.bridge.evidence_dir):
            raise Conflict('trusted_multistep_components_required')
        self.workflow, self.adapter = workflow, adapter
        self.host, self.bridge = workflow.host, workflow.bridge
        self.residency = self.bridge.residency
        self.raw, self.sha = bytes(plan_bytes), expected_sha256
        self.plan = _validate(self.raw, self.sha, workflow)
        self.root = Path(root).absolute()
        self.root.mkdir(mode=0o700, exist_ok=True)
        native.protected_directory(self.root)
        self.path = self.root/'flow.db'
        self.owner_path = workflow.root/'multistep.owner'
        self.meta = dict(plan=self.sha, source=collector_sha256(), route_source=route_sha256(),
                         root=str(self.root), workflow=str(workflow.root), adapter_evidence=str(adapter.evidence_dir.absolute()))
        self.fault = lambda _: None
        self.creator_pid = os.getpid()
        with self._exclusive():
            if self.owner_path.exists():
                if native.protected_read(self.owner_path) != shadow._canonical(self.meta):
                    raise Conflict('workflow_collector_already_bound')
            else:
                shadow._publish(self.owner_path, shadow._canonical(self.meta))
            manifest = self.root/'plan.private.json'
            if manifest.exists():
                if native.protected_read(manifest) != self.raw:
                    raise Conflict('private_plan_changed')
            else:
                shadow._publish(manifest, self.raw)
            self.fresh = workflow._create(self.path)
            with self._transaction(initial=True) as db:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
                old = dict(db.execute('SELECT * FROM meta'))
                if old != self.meta:
                    if old or not self.fresh:
                        raise Conflict('flow_initialization_or_metadata_changed')
                    if _digest(observed_state(self.host.tool, self.host.journal)) != self.plan['initial_state_sha256']:
                        raise Conflict('flow_initial_state_changed')
                    self._pristine()
                    db.executemany('INSERT INTO meta VALUES (?,?)', self.meta.items())
                db.execute('CREATE TABLE IF NOT EXISTS steps (position INTEGER PRIMARY KEY, payload TEXT, digest TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS lifecycle (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT, unload_ms INTEGER)')
                db.execute("INSERT OR IGNORE INTO lifecycle VALUES (1,'open',NULL)")
                if self.fresh:
                    for position in range(len(self.plan['steps'])):
                        self._put(db, dict(position=position, state='pending', phase=None, request_base64=None,
                            observation=None, result=None, blocked_reason=None, fault_armed=False,
                            generation_ms=None, route_ms=None, execution_ms=None, recovery_ms=None))

    def _pristine(self):
        self.workflow._protect()
        if self.workflow.ledger.record_count():
            raise Conflict('workflow_already_used')
        with self.workflow._transaction() as db:
            if db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]:
                raise Conflict('workflow_already_used')
        with self.host._transaction() as db:
            rows = list(db.execute('SELECT * FROM steps'))
        # Allow only a pristine preallocation of the first step, for composition.
        first = _step(self.plan['steps'][0])
        if any(r['state'] != 'allocated' or r['payload'] != _encode_step(first) for r in rows) or len(rows) > 1:
            raise Conflict('host_flow_already_used')
        if self.residency.snapshot()['state'] != 'ready':
            raise Conflict('ready_owned_model_required')

    @contextmanager
    def _exclusive(self):
        import fcntl
        path = self.root/'flow.lock'
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            native.protected_file(path)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict('flow_already_running') from None
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def _transaction(self, initial=False):
        native.protected_directory(self.root)
        native.protected_file(self.path)
        if (not shadow._equal(self.plan, shadow._json(self.raw, self.sha))
                or native.protected_read(self.root/'plan.private.json') != self.raw
                or native.protected_read(self.owner_path) != shadow._canonical(self.meta)
                or collector_sha256() != self.meta['source'] or route_sha256() != self.meta['route_source']
                or str(self.workflow.root) != self.meta['workflow']
                or grants_sha256(self.host.tool) != self.plan['grants_sha256']):
            raise Conflict('multistep_plan_source_or_authority_changed')
        self.workflow._protect()
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if not initial and dict(db.execute('SELECT * FROM meta')) != self.meta:
                raise Conflict('flow_metadata_changed')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _put(db, row):
        db.execute('INSERT OR REPLACE INTO steps VALUES (?,?,?)',
                   (row['position'], shadow._canonical(row).decode(), _digest(row)))

    def _rows(self, db):
        rows = []
        for position, raw, digest in db.execute('SELECT * FROM steps ORDER BY position'):
            row = shadow._json(raw.encode(), digest)
            if row['position'] != position or position != len(rows) or row['state'] not in ('pending', 'attempted', 'complete', 'blocked', 'unresolved'):
                raise Conflict('flow_rows_changed')
            rows.append(row)
        if len(rows) != len(self.plan['steps']):
            raise Conflict('flow_steps_missing')
        unfinished = [r for r in rows if r['state'] in ('pending', 'attempted')]
        if sum(r['state'] == 'attempted' for r in rows) > 1 or any(r['state'] != 'pending' for r in unfinished[1:]):
            raise Conflict('flow_attempt_order_changed')
        return rows

    def _save(self, row):
        with self._transaction() as db:
            self._rows(db)
            self._put(db, row)

    def _public_view(self, step):
        state = observed_state(self.host.tool, self.host.journal)
        return dict(inventory=[r for r in state['inventory'] if r['product'] in step.allowed_products],
                    reservations=[r for r in state['reservations'] if r['task'] == step.task_id
                                  and r['product'] in step.allowed_products])

    def _request(self, case, observation):
        request = json.loads(base64.b64decode(case['request_base64'], validate=True))
        request['input'] += OBSERVATION_SEPARATOR + shadow._canonical(observation).decode()
        request['input_sha256'] = shadow._hash(request['input'].encode())
        raw = shadow._canonical(request)
        native.validate_request(raw, self.bridge.collection_sha)
        return raw

    def run_next(self):
        with self._exclusive():
            with self._transaction() as db:
                rows = self._rows(db)
                row = next((r for r in rows if r['state'] in ('pending', 'attempted')), None)
                if row is None:
                    return None
                if (not self.fresh or os.getpid() != self.creator_pid or row['state'] != 'pending'
                        or db.execute('SELECT state FROM lifecycle').fetchone()[0] != 'open'):
                    raise Conflict('flow_recovery_only')
                case = self.plan['steps'][row['position']]
                observation = self._public_view(_step(case))
                raw = self._request(case, observation)
                row.update(state='attempted', phase='dispatch', observation=observation,
                           request_base64=base64.b64encode(raw).decode())
                self._put(db, row)
            self.fault('attempt_committed')
            step = _step(case)
            self.host.allocate(step)
            started = time.monotonic_ns()
            self.bridge.generate(self.host, step.operation_id, raw, self.adapter)
            row.update(phase='terminal', generation_ms=(time.monotonic_ns()-started)//1_000_000)
            self._save(row)
            self.fault('terminal_recorded')
            started = time.monotonic_ns()
            outcome = self.workflow.decide(step.operation_id)
            row.update(phase='decision', route_ms=(time.monotonic_ns()-started)//1_000_000)
            self._save(row)
            self.fault('decision_recorded')
            if outcome['state'] == 'accepted':
                row['phase'] = 'execution'
                self._save(row)
                started = time.monotonic_ns()
                with self._fault_schedule(row):
                    outcome = self.workflow.execute(step.operation_id)
                row['execution_ms'] = (time.monotonic_ns()-started)//1_000_000
            return self._complete(row, outcome, recovered=False)

    @contextmanager
    def _fault_schedule(self, row):
        old_route, old_effect = self.workflow.fault, self.workflow.executor.fault
        def fire(stage):
            if self.plan['steps'][row['position']]['fault_stage'] == stage and not row['fault_armed']:
                row['fault_armed'] = True
                self._save(row)
                os._exit(86)
        def route(stage):
            old_route(stage)
            if stage == 'execution_intent':
                fire(stage)
        def effect(stage):
            old_effect(stage)
            if stage == 'after_effect':
                fire('tool_effect')
        self.workflow.fault, self.workflow.executor.fault = route, effect
        try:
            yield
        finally:
            self.workflow.fault, self.workflow.executor.fault = old_route, old_effect

    def _verify_attempt(self, row):
        case = self.plan['steps'][row['position']]
        raw = base64.b64decode(row['request_base64'], validate=True)
        if raw != self._request(case, row['observation']):
            raise Conflict('derived_request_changed')
        step = _step(case)
        host_row = self.host.snapshot(step.operation_id)
        if host_row['payload'] != _encode_step(step) or host_row['request_sha256'] != shadow._hash(raw):
            raise Conflict('flow_host_request_changed')
        return self.host.request_binding(step.operation_id)

    def _usage(self, binding):
        # Native evidence has already passed bridge verification. Optional usage
        # remains explicitly unmeasured when absent from that exact response.
        matching = []
        for path in self.bridge.evidence_dir.glob('worker-*.json'):
            trace = json.loads(native.protected_read(path))
            if trace.get('request_sha256') == binding.request_sha256:
                matching.append(trace)
        if len(matching) != 1:
            raise Conflict('unique_native_trace_required')
        response = json.loads(base64.b64decode(matching[0]['wire_response_base64'], validate=True))
        usage = response.get('usage')
        if usage is None:
            return None
        keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        if type(usage) is not dict or any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys):
            return None
        return {key: usage[key] for key in keys}

    def _correct(self, case, result):
        expected = case['expected']
        return (result['route_state'] == expected['route_state']
                and result['effect_code'] == expected['effect_code']
                and result['host_reason'] == expected['host_reason']
                and shadow._equal(result['actual_state'], expected['actual_state']))

    def _complete(self, row, outcome, *, recovered):
        if outcome['state'] not in TERMINAL:
            raise Conflict('flow_effect_or_terminal_decision_unresolved')
        binding = self._verify_attempt(row)
        self.bridge.verify(binding)
        actual = observed_state(self.host.tool, self.host.journal)
        if actual['pending_intents']:
            raise Conflict('flow_effect_unresolved')
        case = self.plan['steps'][row['position']]
        host_row = self.host.snapshot(binding.operation_id)
        result = dict(route_state=outcome['state'], effect_code=outcome['effect_code'],
                      host_reason=host_row['reason'], actual_state=actual,
                      usage=self._usage(binding), recovered=recovered)
        result['correct'] = self._correct(case, result)
        row.update(state='complete', phase='complete', result=result)
        with self._transaction() as db:
            rows = self._rows(db)
            self._put(db, row)
            if outcome['state'] not in case['continue_on']:
                for suffix in rows[row['position']+1:]:
                    if suffix['state'] != 'pending':
                        raise Conflict('flow_suffix_already_attempted')
                    suffix.update(state='blocked', blocked_reason='predecessor:' + outcome['state'])
                    self._put(db, suffix)
        self.fault('step_recorded')
        return result

    def recover_current(self):
        with self._exclusive():
            with self._transaction() as db:
                rows = self._rows(db)
                row = next((r for r in rows if r['state'] == 'attempted'), None)
            if row is None:
                raise Conflict('no_flow_attempt_to_recover')
            started = time.monotonic_ns()
            binding = self._verify_attempt(row)
            try:
                self.bridge.recover(binding)
            except OSError:
                raise Conflict('flow_native_completion_unresolved') from None
            # Never start a decision, or execute a merely accepted proposal.
            outcome = self.workflow.recover(binding.operation_id)
            row['recovery_ms'] = (time.monotonic_ns()-started)//1_000_000
            return self._complete(row, outcome, recovered=True)

    def summary(self):
        with self._transaction() as db:
            rows = self._rows(db)
            lifecycle = db.execute('SELECT state,unload_ms FROM lifecycle').fetchone()
        completed, terminal = [], 0
        for row, case in zip(rows, self.plan['steps']):
            if row['request_base64'] is not None:
                # Unknown completion stays countable rather than becoming success.
                try:
                    binding = self._verify_attempt(row)
                    self.bridge.verify(binding)
                except (Conflict, ValueError, RuntimeError, OSError):
                    if row['state'] in ('complete', 'unresolved'):
                        raise
                else:
                    terminal += 1
            if row['state'] == 'complete':
                result = row['result']
                outcome = self.workflow.snapshot(_step(case).operation_id)
                if (result['correct'] != self._correct(case, result)
                        or result['route_state'] != outcome['state'] or result['effect_code'] != outcome['effect_code']):
                    raise Conflict('flow_result_changed')
                completed.append(row)
        if completed and not any(r['state'] in ('attempted', 'unresolved') for r in rows):
            if completed[-1]['result']['actual_state'] != observed_state(self.host.tool, self.host.journal):
                raise Conflict('flow_final_state_changed')
        exact = lifecycle[0] == 'closed' and self.residency.snapshot()['state'] == 'closed'
        correct = sum(r['result']['correct'] for r in completed)
        faults_armed = all(case['fault_stage'] == 'none' or row['fault_armed']
                           for row, case in zip(rows, self.plan['steps']))
        return dict(route=self.plan['route'], scheduled_steps=len(rows),
            attempted_steps=sum(r['request_base64'] is not None for r in rows), terminal_receipts=terminal,
            missing_terminals=len(rows)-terminal, completed_steps=len(completed), correct_steps=correct,
            blocked_steps=sum(r['state'] == 'blocked' for r in rows),
            blocked_reasons=[r['blocked_reason'] for r in rows if r['state'] == 'blocked'],
            pending_steps=sum(r['state'] == 'pending' for r in rows),
            unresolved_steps=sum(r['state'] in ('attempted', 'unresolved') for r in rows),
            recovered_steps=sum(r['result']['recovered'] for r in completed), fault_schedule_armed=faults_armed,
            armed_faults=[case['fault_stage'] for row, case in zip(rows, self.plan['steps']) if row['fault_armed']],
            generation_ms=[r['generation_ms'] for r in rows], route_ms=[r['route_ms'] for r in rows],
            execution_ms=[r['execution_ms'] for r in rows], recovery_ms=[r['recovery_ms'] for r in rows],
            usage=[r['result']['usage'] if r['result'] else None for r in rows],
            startup_ms=None, end_to_end_ms=None, peak_vram_bytes=None, unload_ms=lifecycle[1], exact_unload=exact,
            flow_passed=len(completed) == len(rows) and correct == len(rows) and terminal == len(rows) and exact and faults_armed)

    def finish(self):
        with self._exclusive():
            with self._transaction() as db:
                rows = self._rows(db)
                unresolved = [r for r in rows if r['state'] == 'attempted']
                for row in unresolved:
                    try:
                        self.bridge.verify(self._verify_attempt(row))
                    except (OSError, ValueError, RuntimeError):
                        raise Conflict('flow_native_completion_unresolved') from None
                    # Explicitly ending a known-terminal but unresolved route
                    # permits model cleanup, never effect execution or success.
                    row['state'] = 'unresolved'
                    self._put(db, row)
                pending = [r for r in rows if r['state'] == 'pending']
                if pending and not unresolved and self.fresh and os.getpid() == self.creator_pid:
                    raise Conflict('flow_not_collected')
                for row in pending:
                    row.update(state='blocked', blocked_reason='unresolved_predecessor' if unresolved else 'restart_uncollected')
                    self._put(db, row)
            self.summary()  # Verify all completed attempts and actual effects before cleanup.
            with self._transaction() as db:
                db.execute("UPDATE lifecycle SET state='closing' WHERE state='open'")
            started = time.monotonic_ns()
            if self.residency.snapshot()['state'] != 'closed':
                self.residency.finish()
            if self.residency.snapshot()['state'] != 'closed' or self.residency._catalog():
                raise Conflict('flow_unload_unverified')
            self.adapter.owned.clear()
            with self._transaction() as db:
                db.execute("UPDATE lifecycle SET state='closed',unload_ms=COALESCE(unload_ms,?)",
                           ((time.monotonic_ns()-started)//1_000_000,))
        return self.summary()
