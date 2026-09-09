"""Durable host allocation and trusted-evidence recovery, without real models."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.controlled_tool_proposal import HostStep
from tools.controlled_tools import Conflict, Executor, IntentJournal, ToolStore, Operation
from tools.host_steps import HostStepStore, TerminalEvidence

REQUEST = '1'*64
POLICY = '2'*64


def step(**changes):
    values = dict(task_id='task-a', step_id='step-1', operation_id='op-1',
                  allowed_kinds=('reserve','release'), allowed_products=('widget',),
                  max_quantity=5, reservations=())
    values.update(changes)
    return HostStep(**values)


def proposal(**changes):
    values = dict(schema_version='controlled-tool-proposal/1.0', decision='reserve',
                  product='widget', quantity=4, expected_version=0,
                  reservation_id=None, reason='none')
    values.update(changes)
    return json.dumps(values)


def verified(binding, content=None, **changes):
    # Trusted verifier stub; these tests do not certify a native wire receipt.
    receipt = dict(schema_version='hermes-shadow-terminal/1.0',
                   request_sha256=binding.request_sha256, server_finished=True,
                   status='completed', content=proposal() if content is None else content)
    receipt.update(changes)
    return TerminalEvidence(binding.policy_sha256, json.dumps(receipt).encode())


def stores(root, verifier=verified, fault=None):
    tool=ToolStore(root/'tool.sqlite3')
    journal=IntentJournal(root/'intent.sqlite3')
    return HostStepStore(root/'host.sqlite3', journal=journal, tool=tool,
                         terminal_verifier=verifier, fault=fault), Executor(journal,tool)


class HostStepTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store,self.executor=stores(self.root)
        self.tool=self.executor.tool
        self.tool.seed('widget',10)
        self.tool.authorize('task-a','widget',7)

    def begin(self):
        self.store.allocate(step())
        return self.store.begin_request('op-1',REQUEST,POLICY)

    def test_reopen_preserves_exact_scope_and_allocation(self):
        first=self.store.allocate(step())
        other,_=stores(self.root)
        self.assertEqual(other.allocate(step()),first)
        self.assertEqual(other.host_step('op-1'),step())
        self.assertTrue(other.verify_journal())
        self.assertIsNone(self.executor.journal.pending('task-a'))
        self.assertEqual(self.tool.inventory('widget')['available'],10)

    def test_concurrent_duplicates_allocate_one_step(self):
        def allocate(_): return stores(self.root)[0].allocate(step())
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(allocate,range(8)))
        self.assertTrue(all(value==results[0] for value in results))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM steps').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_identity_scope_or_active_step_changes_conflict(self):
        self.store.allocate(step())
        for changed in (step(max_quantity=6), step(operation_id='replacement'),
                        step(step_id='changed'), step(task_id='task-b'),
                        step(step_id='next',operation_id='op-2')):
            with self.subTest(changed=changed),self.assertRaises(Conflict): self.store.allocate(changed)

    def test_request_binding_is_durable_and_not_a_repeat_send_lease(self):
        binding=self.begin()
        other,_=stores(self.root)
        self.assertEqual(other.request_binding('op-1'),binding)
        for request,policy in ((REQUEST,POLICY),('3'*64,POLICY),(REQUEST,'4'*64)):
            with self.assertRaises(Conflict):other.begin_request('op-1',request,policy)
        with self.assertRaises(Conflict):other.allocate(step(step_id='next',operation_id='op-2'))

    def test_unverified_completion_remains_requesting(self):
        self.begin()
        for verifier in (lambda _:None, lambda _:True,
                         lambda b:{'server_finished':True,'status':'completed'},
                         lambda b:TerminalEvidence('3'*64,verified(b).receipt_bytes),
                         lambda b:verified(b,request_sha256='3'*64),
                         lambda b:verified(b,server_finished=1),
                         lambda b:verified(b,extra='untrusted'),
                         lambda b:TerminalEvidence(POLICY,b'[]')):
            other,_=stores(self.root,verifier=verifier)
            with self.subTest(verifier=verifier),self.assertRaises(Conflict):other.record_terminal('op-1')
            self.assertEqual(other.snapshot('op-1')['state'],'requesting')
        with self.assertRaises(TypeError):self.store.record_terminal('op-1',{'status':'completed'})

    def test_verifier_failure_preserves_binding_and_no_effect(self):
        self.begin()
        def unavailable(_):raise OSError('lookup unavailable')
        other,_=stores(self.root,verifier=unavailable)
        with self.assertRaises(OSError):other.record_terminal('op-1')
        self.assertEqual(other.request_binding('op-1').request_sha256,REQUEST)
        self.assertIsNone(self.tool.lookup('op-1'))

    def test_proposal_persistence_does_not_execute_or_complete_task(self):
        binding=self.begin()
        record=self.store.record_terminal('op-1')
        self.assertEqual(record['state'],'proposed')
        self.assertEqual(record['terminal_sha256'],hashlib.sha256(verified(binding).receipt_bytes).hexdigest())
        self.assertEqual(self.store.bound_proposal('op-1').operation.operation_id,'op-1')
        self.assertIsNone(self.tool.lookup('op-1'))
        with self.assertRaises(Conflict):self.store.reconcile_effect('op-1')
        with self.assertRaises(Conflict):self.store.allocate(step(step_id='next',operation_id='op-2'))

    def test_completed_effect_requires_both_intent_and_tool_receipt(self):
        self.begin();self.store.record_terminal('op-1')
        receipt=self.executor.execute(self.store.bound_proposal('op-1').operation)
        self.assertEqual(self.store.reconcile_effect('op-1')['state'],'applied')
        other,_=stores(self.root)
        self.assertEqual(other.snapshot('op-1')['effect_sha256'],hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest())
        other.allocate(step(step_id='next',operation_id='op-2'))
        with self.assertRaises(Conflict):other.begin_request('op-2',REQUEST,POLICY)

    def test_pending_effect_blocks_new_allocation_and_request(self):
        outside=Operation('external','task-a','external','reserve','widget',1,0)
        self.executor.journal.prepare(outside)
        with self.assertRaises(Conflict):self.store.allocate(step())
        self.executor.recover('task-a')
        self.store.allocate(step())
        pending=replace(outside,operation_id='external-2',step_id='external-2',expected_version=1)
        self.executor.journal.prepare(pending)
        with self.assertRaises(Conflict):self.store.begin_request('op-1',REQUEST,POLICY)

    def test_abstention_cannot_clear_another_pending_effect(self):
        self.begin()
        outside=Operation('external','task-a','external','reserve','widget',1,0)
        self.executor.journal.prepare(outside)
        abstain=lambda b:verified(b,content=proposal(decision='abstain',product=None,quantity=None,
                                                   expected_version=None,reason='no_action'))
        other,_=stores(self.root,verifier=abstain)
        with self.assertRaises(Conflict):other.record_terminal('op-1')
        self.assertEqual(self.executor.journal.pending('task-a')['id'],'external')
        self.executor.recover('task-a')
        self.assertEqual(other.record_terminal('op-1')['state'],'abstained')
        self.assertIsNone(other.bound_proposal('op-1').operation)

    def test_invalid_proposal_and_verified_generation_failure_are_distinct(self):
        for number,verifier,expected in (
            (1,lambda b:verified(b,content=proposal(quantity=999)),'invalid'),
            (2,lambda b:TerminalEvidence(POLICY,json.dumps({'schema_version':'hermes-shadow-terminal/1.0',
             'request_sha256':b.request_sha256,'server_finished':True,'status':'failed','error_code':'budget_exhausted'}).encode()),'generation_failed')):
            op='invalid-'+str(number)
            self.store.allocate(step(step_id=op,operation_id=op))
            self.store.begin_request(op,str(number+4)*64,POLICY)
            other,_=stores(self.root,verifier=verifier)
            self.assertEqual(other.record_terminal(op)['state'],expected)
            self.assertIsNone(self.tool.lookup(op))

    def test_tool_rejection_is_preserved_not_success(self):
        self.begin()
        other,_=stores(self.root,verifier=lambda b:verified(b,content=proposal(expected_version=9)))
        other.record_terminal('op-1')
        receipt=self.executor.execute(other.bound_proposal('op-1').operation)
        self.assertEqual(receipt['code'],'version_mismatch')
        self.assertEqual(other.reconcile_effect('op-1')['state'],'rejected')
        self.assertEqual(self.tool.inventory('widget')['available'],10)

    def test_lookup_failure_or_mismatch_does_not_finalize_effect(self):
        self.begin();self.store.record_terminal('op-1')
        self.executor.execute(self.store.bound_proposal('op-1').operation)
        for result in (None, {'operation_id':'fake'}):
            with patch.object(self.tool,'lookup',return_value=result),self.assertRaises(Conflict):
                self.store.reconcile_effect('op-1')
        with patch.object(self.tool,'lookup',side_effect=OSError('unavailable')),self.assertRaises(OSError):
            self.store.reconcile_effect('op-1')
        self.assertEqual(self.store.snapshot('op-1')['state'],'proposed')

    def test_altered_serialized_scope_and_journal_fail_closed(self):
        self.store.allocate(step())
        with closing(sqlite3.connect(self.store.path)) as db, db:
            payload=json.loads(db.execute('SELECT payload FROM steps').fetchone()[0])
            payload['max_quantity']=99
            raw=json.dumps(payload,sort_keys=True,separators=(',',':'))
            db.execute('UPDATE steps SET payload=?,step_sha256=?',(raw,hashlib.sha256(raw.encode()).hexdigest()))
        with self.assertRaises(Conflict):self.store.host_step('op-1')
        with self.assertRaises(Conflict):self.store.verify_journal()

    def test_database_identity_binding_and_path_separation(self):
        other_journal=IntentJournal(self.root/'other-intents.sqlite3')
        with self.assertRaises(Conflict):
            HostStepStore(self.store.path,journal=other_journal,tool=self.tool,terminal_verifier=verified)
        with self.assertRaises(ValueError):
            HostStepStore(self.tool.path,journal=self.executor.journal,tool=self.tool,terminal_verifier=verified)
        with self.assertRaises(ValueError):
            HostStepStore(self.root/'untrusted.sqlite3',journal=self.executor.journal,tool=self.tool,terminal_verifier=None)

    def test_changed_live_database_binding_rejected(self):
        self.store.allocate(step())
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("UPDATE meta SET value='different' WHERE key='effect_path'")
        with self.assertRaises(Conflict):self.store.snapshot('op-1')

    def test_conflicting_terminal_race_does_not_replace_first_result(self):
        self.begin()
        def racing_verifier(binding):
            self.store.record_terminal('op-1')
            return verified(binding,content=proposal(quantity=3))
        other,_=stores(self.root,verifier=racing_verifier)
        with self.assertRaises(Conflict):other.record_terminal('op-1')
        self.assertEqual(self.store.bound_proposal('op-1').operation.quantity,4)

    def test_verification_record_with_duplicate_keys_is_rejected(self):
        self.begin()
        def duplicate(binding):
            raw=verified(binding).receipt_bytes
            return TerminalEvidence(POLICY,raw[:-1]+b',"server_finished":true}')
        other,_=stores(self.root,verifier=duplicate)
        with self.assertRaises(Conflict):other.record_terminal('op-1')
        self.assertEqual(other.snapshot('op-1')['state'],'requesting')

    def test_real_process_crashes_preserve_recovery_and_single_effect(self):
        for stage in ('after_allocation','after_request','after_proposal','after_tool_effect','after_effect_record'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                code='''import os,sys
from pathlib import Path
from test_host_steps import stores,step,REQUEST,POLICY
root=Path(sys.argv[1]);stage=sys.argv[2]
def fault(point):
 if point==stage:os._exit(19)
store,executor=stores(root,fault=fault)
executor.tool.seed('widget',10);executor.tool.authorize('task-a','widget',7)
store.allocate(step());store.begin_request('op-1',REQUEST,POLICY);store.record_terminal('op-1')
executor.fault=lambda point:fault('after_tool_effect' if point=='after_effect' else 'unused')
executor.execute(store.bound_proposal('op-1').operation);store.reconcile_effect('op-1')
'''
                env=dict(os.environ,PYTHONPATH=os.pathsep.join(map(str, (
                    Path(__file__).resolve().parent, Path(__file__).resolve().parents[1],
                    Path(__file__).resolve().parents[1]/'src'))))
                child=subprocess.run([sys.executable,'-c',code,str(root),stage],env=env,capture_output=True,timeout=20)
                self.assertEqual(child.returncode,19,child.stderr.decode(errors='replace'))
                store,executor=stores(root)
                expected={'after_allocation':'allocated','after_request':'requesting','after_proposal':'proposed',
                          'after_tool_effect':'proposed','after_effect_record':'applied'}[stage]
                self.assertEqual(store.snapshot('op-1')['state'],expected)
                self.assertTrue(store.verify_journal())
                if stage=='after_allocation':store.begin_request('op-1',REQUEST,POLICY)
                elif stage=='after_request':
                    with self.assertRaises(Conflict):store.begin_request('op-1',REQUEST,POLICY)
                if expected in ('allocated','requesting'):store.record_terminal('op-1')
                if stage=='after_tool_effect':
                    with self.assertRaises(Conflict):store.reconcile_effect('op-1')
                    executor.recover('task-a')
                else:executor.execute(store.bound_proposal('op-1').operation)
                self.assertEqual(store.reconcile_effect('op-1')['state'],'applied')
                self.assertEqual(executor.tool.inventory('widget')['available'],6)


if __name__=='__main__':unittest.main()
