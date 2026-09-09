"""Bounded, preregistered native simulator collection; no live external effects.

Only a freshly created collector may send never-attempted requests. Reopening
permits evidence/effect recovery and exact cleanup, never LLM replay. Reference
answers stay in a private host manifest and never enter native worker arguments.
"""
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time

from hermes_dohaa.learning import native_prompt as native, shadow
from tools.controlled_tools import Conflict, Executor
from tools.host_steps import HostStepStore
from tools.native_model_residency import ModelResidency, ResidencyError
from tools.native_tool_evidence import NativeToolEvidenceBridge, verifier_sha256


def collector_sha256():
    return shadow._hash(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))


def observed_state(tool, journal):
    """Observe actual persisted effects, including reservation ownership."""
    with tool.transaction() as db:
        inventory = [dict(row) for row in db.execute('SELECT * FROM inventory ORDER BY product')]
        reservations = [dict(row) for row in db.execute('SELECT * FROM reservations ORDER BY id')]
        receipts = db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
    with journal.transaction() as db:
        complete = db.execute("SELECT COUNT(*) FROM intents WHERE state='complete'").fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM intents WHERE state='pending'").fetchone()[0]
    return dict(inventory=inventory, reservations=reservations, effect_receipts=receipts,
                completed_intents=complete, pending_intents=pending)


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high: raise Conflict('invalid_plan_integer')


def _state(value):
    shadow._fields(value, {'inventory','reservations','effect_receipts','completed_intents','pending_intents'})
    for key in ('effect_receipts','completed_intents','pending_intents'): _integer(value[key],0,65536)
    for key, fields, identity in (('inventory', {'product','available','version'}, 'product'),
                                  ('reservations', {'id','task','product','quantity','active'}, 'id')):
        if type(value[key]) is not list or len(value[key]) > 128: raise Conflict('invalid_expected_state')
        ids = []
        for row in value[key]:
            shadow._fields(row,fields)
            for field in fields:
                if field in ('available','version','quantity','active'):
                    _integer(row[field], 1 if field=='quantity' else 0, 1 if field=='active' else 2**31-1)
                elif type(row[field]) is not str or not 1 <= len(row[field]) <= 128:
                    raise Conflict('invalid_state_identifier')
            ids.append(row[identity])
        if ids != sorted(set(ids)): raise Conflict('state_order_or_duplicates')


def validate_plan(raw, digest, host, bridge):
    plan = shadow._json(raw,digest)
    shadow._fields(plan, {'schema_version','collector_sha256','runtime_policy_sha256',
                         'collection_policy_sha256','initial_state_sha256','minimum_correct','cases'})
    if (plan['schema_version'] != 'hermes-native-tool-canary/1.0'
            or plan['collector_sha256'] != collector_sha256()
            or plan['runtime_policy_sha256'] != bridge.policy_sha
            or plan['collection_policy_sha256'] != bridge.collection_sha):
        raise Conflict('canary_source_or_policy_changed')
    shadow._digest(plan['initial_state_sha256'])
    if type(plan['cases']) is not list or not 1 <= len(plan['cases']) <= 16:
        raise Conflict('bounded_cases_required')
    _integer(plan['minimum_correct'],1,len(plan['cases']))
    ids, operations, requests = set(), set(), set()
    for case in plan['cases']:
        shadow._fields(case, {'case_id','operation_id','step_sha256','request_base64',
                             'expected_host_state','expected_reason','expected_effect_code','expected_state'})
        if type(case['case_id']) is not str or not 1 <= len(case['case_id']) <= 128:
            raise Conflict('invalid_case_id')
        row = host.snapshot(case['operation_id'])
        if row['step_sha256'] != case['step_sha256']: raise Conflict('plan_scope_changed')
        request = base64.b64decode(case['request_base64'],validate=True)
        native.validate_request(request, bridge.collection_sha)
        request_sha = shadow._hash(request)
        if case['case_id'] in ids or case['operation_id'] in operations or request_sha in requests:
            raise Conflict('case_or_request_reused')
        ids.add(case['case_id']); operations.add(case['operation_id']); requests.add(request_sha)
        if case['expected_host_state'] not in ('applied','rejected','abstained'):
            raise Conflict('invalid_expected_outcome')
        code=case['expected_effect_code']
        if ((case['expected_host_state']=='applied' and code!='applied')
                or (case['expected_host_state']=='abstained' and code is not None)
                or (case['expected_host_state']=='rejected' and code not in ('not_authorized','unknown_product',
                    'version_mismatch','quota_exceeded','insufficient_stock','reservation_mismatch','reservation_inactive'))):
            raise Conflict('invalid_expected_effect_code')
        if ((case['expected_host_state']!='abstained' and case['expected_reason']!=code)
                or (case['expected_host_state']=='abstained' and case['expected_reason'] not in
                    ('no_action','inventory_unavailable','insufficient_stock','conflicting_sources'))):
            raise Conflict('invalid_expected_reason')
        _state(case['expected_state'])
    return plan


class CanaryCollector:
    def __init__(self, root, *, plan_bytes, expected_sha256, host, bridge, adapter, residency, executor):
        if (type(host) is not HostStepStore or type(bridge) is not NativeToolEvidenceBridge
                or type(adapter) is not native.NativePromptAdapter or type(residency) is not ModelResidency
                or type(executor) is not Executor or bridge.residency is not residency
                or host.terminal_verifier != bridge.verify or executor.tool is not host.tool
                or executor.journal is not host.journal):
            raise Conflict('trusted_canary_components_required')
        self.host,self.bridge,self.adapter,self.residency,self.executor = host,bridge,adapter,residency,executor
        self.raw = bytes(plan_bytes); self.sha = expected_sha256
        self.plan = validate_plan(self.raw,self.sha,host,bridge)
        self.root = Path(root).absolute(); self.root.mkdir(mode=0o700,exist_ok=True)
        native.protected_directory(self.root)
        manifest = self.root/'manifest.private.json'
        if manifest.exists():
            if native.protected_read(manifest) != self.raw: raise Conflict('manifest_changed')
        else: shadow._publish(manifest,self.raw)
        self.path = self.root/'collection.db'
        try: fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError: self.fresh=False
        else: os.close(fd); self.fresh=True
        self.fault=lambda _:None
        self.meta={'manifest':self.sha,'collector':collector_sha256(),'verifier':verifier_sha256(),
                   'host':str(host.path.absolute()),'bridge':str(bridge.path.absolute()),
                   'residency':str(residency.path.absolute())}
        with self._transaction(initial=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
            old=dict(db.execute('SELECT * FROM meta'))
            if old and old!=self.meta: raise Conflict('collector_metadata_changed')
            if not old:
                if not self.fresh: raise Conflict('collector_initialization_unresolved')
                actual=observed_state(host.tool,host.journal)
                if shadow._hash(shadow._canonical(actual))!=self.plan['initial_state_sha256']:
                    raise Conflict('initial_state_changed')
                if any(host.snapshot(c['operation_id'])['state']!='allocated' for c in self.plan['cases']):
                    raise Conflict('host_request_already_attempted')
                db.executemany('INSERT INTO meta VALUES (?,?)', self.meta.items())
            db.execute("CREATE TABLE IF NOT EXISTS cases (position INTEGER PRIMARY KEY, state TEXT, result TEXT, digest TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS lifecycle (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT, unload_ms INTEGER)")
            db.execute("INSERT OR IGNORE INTO lifecycle VALUES (1,'open',NULL)")
            if self.fresh:
                db.executemany("INSERT INTO cases VALUES (?,'pending',NULL,NULL)",[(i,) for i in range(len(self.plan['cases']))])

    @contextmanager
    def _transaction(self, initial=False):
        native.protected_file(self.path)
        if not shadow._equal(self.plan,shadow._json(self.raw,self.sha)):
            raise Conflict('in_memory_manifest_changed')
        if native.protected_read(self.root/'manifest.private.json')!=self.raw:
            raise Conflict('manifest_changed')
        if collector_sha256()!=self.meta['collector'] or verifier_sha256()!=self.meta['verifier']:
            raise Conflict('collector_source_changed')
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            if not initial and dict(db.execute('SELECT * FROM meta'))!=self.meta:
                raise Conflict('collector_metadata_changed')
            yield db;db.commit()
        except BaseException: db.rollback();raise
        finally: db.close()

    @contextmanager
    def _exclusive(self):
        import fcntl
        descriptor=os.open(self.root/'collector.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            native.protected_file(self.root/'collector.lock')
            try: fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Conflict('collector_already_running') from None
            yield
        finally: os.close(descriptor)

    def _next(self, db):
        rows=db.execute('SELECT * FROM cases ORDER BY position').fetchall()
        if [r['position'] for r in rows]!=list(range(len(self.plan['cases']))):
            raise Conflict('case_rows_changed')
        unfinished=[r for r in rows if r['state']!='complete']
        if any(r['state'] not in ('pending','attempted','complete') for r in rows):
            raise Conflict('case_state_changed')
        return dict(unfinished[0]) if unfinished else None

    def run_next(self):
        with self._exclusive():
            with self._transaction() as db:
                row=self._next(db)
                if row is None: return None
                if (not self.fresh or row['state']!='pending'
                        or db.execute('SELECT state FROM lifecycle').fetchone()[0]!='open'):
                    raise Conflict('recovery_only_or_request_attempted')
                db.execute("UPDATE cases SET state='attempted' WHERE position=?",(row['position'],))
            self.fault('attempt_committed')
            case=self.plan['cases'][row['position']]
            started=time.monotonic_ns()
            self.bridge.generate(self.host,case['operation_id'],base64.b64decode(case['request_base64'],validate=True),self.adapter)
            elapsed=(time.monotonic_ns()-started)//1_000_000
            self.fault('terminal_verified')
            return self._complete(row['position'],elapsed,recover=False)

    def _complete(self, position, elapsed, *, recover):
        case=self.plan['cases'][position];opid=case['operation_id']
        row=self.host.snapshot(opid)
        if row['state']=='allocated': raise Conflict('attempt_without_terminal_evidence')
        if recover: self.bridge.recover(self.host.request_binding(opid))
        row=self.host.record_terminal(opid)
        if row['state']=='proposed':
            operation=self.host.bound_proposal(opid).operation
            if self.executor.journal.pending(operation.task_id): self.executor.recover(operation.task_id)
            else: self.executor.execute(operation)
            row=self.host.reconcile_effect(opid)
        self.fault('effect_observed')
        actual=observed_state(self.host.tool,self.host.journal)
        if actual['pending_intents']: raise Conflict('effect_completion_unresolved')
        effect=self.host.tool.lookup(opid)
        effect_code=effect['code'] if effect else None
        result={'host_state':row['state'],'reason':row['reason'],'actual_state':actual,
                'effect_code':effect_code,'request_elapsed_ms':elapsed,'recovered':recover,
                'correct':row['state']==case['expected_host_state'] and row['reason']==case['expected_reason']
                    and effect_code==case['expected_effect_code']
                    and shadow._equal(actual,case['expected_state'])}
        raw=shadow._canonical(result)
        with self._transaction() as db:
            row=db.execute('SELECT * FROM cases WHERE position=?',(position,)).fetchone()
            if row['state']!='attempted': raise Conflict('case_completion_changed')
            db.execute("UPDATE cases SET state='complete',result=?,digest=? WHERE position=?",
                       (raw.decode(),shadow._hash(raw),position))
        return result

    def recover_current(self):
        with self._exclusive():
            with self._transaction() as db: row=self._next(db)
            if row is None: return None
            if row['state']!='attempted': raise Conflict('no_attempt_to_recover')
            return self._complete(row['position'],None,recover=True)

    def summary(self):
        with self._transaction() as db:
            self._next(db)
            rows=[dict(r) for r in db.execute('SELECT * FROM cases ORDER BY position')]
            lifecycle=dict(db.execute('SELECT * FROM lifecycle').fetchone())
        completed=[];terminals=0
        for row,case in zip(rows,self.plan['cases']):
            state=self.host.snapshot(case['operation_id'])['state']
            if state not in ('allocated','requesting'):
                self.bridge.verify(self.host.request_binding(case['operation_id']));terminals+=1
            if row['state']=='complete':
                result=shadow._json(row['result'].encode(),row['digest'])
                expected=case['expected_state']
                correct=(result['host_state']==case['expected_host_state'] and result['reason']==case['expected_reason']
                         and result['effect_code']==case['expected_effect_code'] and shadow._equal(result['actual_state'],expected))
                if type(result['correct']) is not bool or result['correct']!=correct or state!=result['host_state']:
                    raise Conflict('stored_verdict_changed')
                completed.append(result)
        if completed and shadow._equal(completed[-1]['actual_state'], observed_state(self.host.tool,self.host.journal)) is False:
            # A running next case may already have a verified effect awaiting its
            # row commit. Do not report final state until explicit recovery.
            if not any(r['state']=='attempted' for r in rows): raise Conflict('final_state_changed')
        n=len(rows);correct=sum(r['correct'] for r in completed)
        exact=lifecycle['state']=='closed' and self.residency.snapshot()['state']=='closed'
        return {'scheduled':n,'send_attempts':sum(r['state']!='pending' for r in rows),
                'terminal_receipts':terminals,'missing_terminals':n-terminals,'completed_cases':len(completed),
                'correct':correct,'applied':sum(r['host_state']=='applied' for r in completed),
                'abstained':sum(r['host_state']=='abstained' for r in completed),
                'generation_failed':sum(r['host_state']=='generation_failed' for r in completed),
                'invalid':sum(r['host_state']=='invalid' for r in completed),
                'recovered_cases':sum(r['recovered'] for r in completed),
                'request_elapsed_ms':[r['request_elapsed_ms'] for r in completed],
                'exact_unload':exact,'unload_ms':lifecycle['unload_ms'],
                'criterion_passed':len(completed)==n and terminals==n and correct>=self.plan['minimum_correct'] and exact}

    def finish(self):
        with self._exclusive():
            with self._transaction() as db:
                if self._next(db) is not None: raise Conflict('incomplete_canary')
                db.execute("UPDATE lifecycle SET state='closing' WHERE state='open'")
            started=time.monotonic_ns()
            row=self.residency.snapshot()
            if row['state']!='closed': self.residency.finish()
            if self.residency.snapshot()['state']!='closed' or self.residency._catalog():
                raise Conflict('unload_unverified')
            # Durable ownership is the only unload authority. Never run the
            # adapter's volatile unload loop after a collector process restart.
            self.adapter.owned.clear()
            with self._transaction() as db:
                db.execute("UPDATE lifecycle SET state='closed',unload_ms=COALESCE(unload_ms,?)",
                           ((time.monotonic_ns()-started)//1_000_000,))
        return self.summary()
