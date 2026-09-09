"""Actual controller and simulator integration with synthetic native transport."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hermes_dohaa.controller.engine import DohaaController, RunResult, RunStatus, RunReasonCode
from hermes_dohaa.learning import native_prompt as native, shadow
from hermes_dohaa.runtime.base import Proposal
from tools.controlled_tools import Conflict, Executor, Operation
import test_native_tool_evidence as fixtures


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'privileged tool workflow')
class ToolRouteTests(unittest.TestCase):
    def setUp(self):
        from tools import controlled_tool_routes as routes
        self.routes=routes
        self.f=fixtures.ToolEvidenceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.send()
        self.executor=Executor(self.f.intent,self.f.tool)
        self.workflow=self.open()

    def open(self, route='dohaa'):
        obj=self.routes.ToolWorkflow(self.f.root/'route',route=route,host=self.f.host,
            bridge=self.f.bridge,executor=self.executor)
        self.addCleanup(obj.close)
        return obj

    def test_actual_controller_gate_ledger_and_acceptance_without_effect(self):
        calls=[];original=DohaaController.run
        def observed(controller,contract,**kwargs):
            calls.append(contract);return original(controller,contract,**kwargs)
        with patch.object(DohaaController,'run',observed): row=self.workflow.decide('op-1')
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].max_attempts,1)
        self.assertEqual(calls[0].allowed_actions,frozenset({'simulator.reserve','simulator.release'}))
        self.assertNotIn('semantic_assertions',calls[0].inputs)
        self.assertEqual(row['state'],'accepted');self.assertFalse(row['effect_applied'])
        self.assertNotIn('task_success',row)
        types=[r.event_type for r in self.workflow.ledger.records()]
        self.assertIn('run.received',types);self.assertIn('gates.evaluated',types);self.assertIn('run.finished',types)
        self.assertIsNone(self.f.tool.lookup('op-1'))
        self.assertEqual(self.workflow.recover('op-1')['state'],'accepted')
        self.assertEqual(self.f.sends,1)

    def test_effect_requires_acceptance_and_actual_matching_receipts(self):
        with self.assertRaises(Conflict):self.workflow.execute('op-1')
        self.workflow.decide('op-1');result=self.workflow.execute('op-1')
        self.assertEqual(result['state'],'applied');self.assertTrue(result['effect_applied'])
        self.assertEqual(self.f.tool.inventory('widget')['available'],6)
        self.assertEqual(self.workflow.execute('op-1'),result)

    def test_simple_route_uses_real_effect_without_calling_controller(self):
        self.workflow.close()
        f=fixtures.ToolEvidenceTests();f.setUp();self.addCleanup(f.doCleanups);f.send()
        simple=self.routes.ToolWorkflow(f.root/'simple',route='simple',host=f.host,bridge=f.bridge,
                                       executor=Executor(f.intent,f.tool));self.addCleanup(simple.close)
        with patch.object(DohaaController,'run',side_effect=AssertionError('must use separate simple route')):
            self.assertEqual(simple.decide('op-1')['state'],'accepted')
            self.assertTrue(simple.execute('op-1')['effect_applied'])
        self.workflow=self.open();self.workflow.decide('op-1');self.workflow.execute('op-1')
        self.assertEqual(f.tool.inventory('widget'),self.f.tool.inventory('widget'))

    def test_altered_runtime_proposal_is_denied_by_actual_controller(self):
        original=self.routes._VerifiedRuntime.propose
        def altered(runtime,contract,feedback=()):
            result=original(runtime,contract,feedback);data=result.to_dict()
            data['result']['tool_proposal']['quantity']=1
            return Proposal.from_dict(data)
        with patch.object(self.routes._VerifiedRuntime,'propose',altered):
            result=self.workflow.decide('op-1')
        self.assertEqual(result['state'],'denied');self.assertFalse(result['effect_applied'])
        self.assertEqual(self.workflow.execute('op-1')['state'],'denied')
        self.assertIsNone(self.f.tool.lookup('op-1'))
        self.assertEqual(self.f.host.snapshot('op-1')['state'],'proposed')
        with self.assertRaises(Conflict):self.f.host.allocate(replace(self.f.host.host_step('op-1'),operation_id='new'))

    def test_forged_controller_success_without_ledger_is_not_authority(self):
        forged=RunResult('made-up',RunStatus.SUCCEEDED,1,None,(),RunReasonCode.SUCCEEDED,'fake')
        with patch.object(DohaaController,'run',return_value=forged):
            with self.assertRaises(Conflict):self.workflow.decide('op-1')
        with self.assertRaises(Conflict):self.workflow.execute('op-1')
        with self.assertRaises(Conflict):self.workflow.recover('op-1')
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_semantic_repair_cannot_manufacture_tool_admission(self):
        from hermes_dohaa.controller.semantic_repair import DeterministicSemanticRepair
        original=self.routes._VerifiedRuntime.propose;saved=[]
        def altered(runtime,contract,feedback=()):
            proposal=original(runtime,contract,feedback);saved.append(proposal)
            raw=proposal.to_dict();raw['requested_actions']=['shell.exec'];return Proposal.from_dict(raw)
        def repair(*a,**kw):return DeterministicSemanticRepair(saved[0],('invented',),('/requested_actions',))
        with patch.object(self.routes._VerifiedRuntime,'propose',altered),patch(
            'hermes_dohaa.controller.engine.propose_deterministic_semantic_repair',repair):
            with self.assertRaises(Conflict):self.workflow.decide('op-1')
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_duplicate_decide_does_not_repeat_controller(self):
        self.workflow.decide('op-1')
        with patch.object(DohaaController,'run',side_effect=AssertionError('duplicate decision')):
            self.assertEqual(self.workflow.decide('op-1')['state'],'accepted')

    def test_stale_version_has_verified_rejected_effect(self):
        self.workflow.decide('op-1')
        with self.f.tool.transaction() as db:db.execute('UPDATE inventory SET version=1')
        result=self.workflow.execute('op-1')
        self.assertEqual((result['state'],result['effect_code']),('rejected','version_mismatch'))
        self.assertFalse(result['effect_applied']);self.assertEqual(self.f.tool.inventory('widget')['available'],10)

    def test_independent_quota_refusal(self):
        with self.f.tool.transaction() as db:db.execute('UPDATE grants SET quantity=3')
        self.workflow.decide('op-1')
        self.assertEqual(self.workflow.execute('op-1')['effect_code'],'quota_exceeded')

    def test_independent_permission_refusal(self):
        with self.f.tool.transaction() as db:db.execute('DELETE FROM grants')
        self.workflow.decide('op-1')
        self.assertEqual(self.workflow.execute('op-1')['effect_code'],'not_authorized')

    def test_pending_other_effect_blocks_decision_and_execution(self):
        self.f.intent.prepare(Operation('other','task-a','other-step','reserve','widget',1,0))
        with self.assertRaises(Conflict):self.workflow.decide('op-1')
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_pending_other_effect_after_acceptance_is_not_cleared(self):
        self.workflow.decide('op-1')
        self.f.intent.prepare(Operation('other','task-a','other-step','reserve','widget',1,0))
        with self.assertRaises(Conflict):self.workflow.execute('op-1')
        self.assertIsNotNone(self.f.intent.pending('task-a'))

    def test_changed_native_evidence_blocks_accepted_execution(self):
        self.workflow.decide('op-1')
        path=self.f.adapter.evidence_dir/'worker-0000.json';raw=json.loads(path.read_bytes());raw['actual_requests']=2
        path.write_bytes(shadow._canonical(raw))
        with self.assertRaises((Conflict,ValueError,native.NativePromptError)):self.workflow.execute('op-1')
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_changed_effect_receipt_blocks_final_report(self):
        self.workflow.decide('op-1');self.workflow.execute('op-1')
        with self.f.tool.transaction() as db:db.execute("UPDATE receipts SET receipt='{}' WHERE id='op-1'")
        with self.assertRaises(Conflict):self.workflow.snapshot('op-1')

    def test_route_cannot_change_or_attach_to_same_host_twice(self):
        with self.assertRaises(Conflict):self.open('simple')
        with self.assertRaises(Conflict):self.routes.ToolWorkflow(self.f.root/'second',route='dohaa',
            host=self.f.host,bridge=self.f.bridge,executor=self.executor)

    def test_controller_gate_ledger_mutation_blocks_execution(self):
        self.workflow.decide('op-1')
        with self.workflow.ledger._connection:
            self.workflow.ledger._connection.execute("UPDATE ledger_events SET payload_json='{}' WHERE event_type='gates.evaluated'")
        with self.assertRaises((Conflict,RuntimeError)):self.workflow.execute('op-1')

    def test_worker_cannot_read_or_write_decision_and_ledger(self):
        self.workflow.decide('op-1')
        script='import sys\nfor p in sys.argv[1:]:\n for mode in ("rb","ab"):\n  try:open(p,mode).close()\n  except PermissionError:continue\n  raise SystemExit(8)\n'
        result=subprocess.run([sys.executable,'-c',script,str(self.workflow.path),self.workflow.ledger.path],
            user=self.f.p['worker_uid'],group=self.f.p['worker_gid'],extra_groups=[],cwd='/tmp')
        self.assertEqual(result.returncode,0)

    def separate(self, route='dohaa', content=None):
        f=fixtures.ToolEvidenceTests();f.setUp();self.addCleanup(f.doCleanups)
        if content is None:f.send()
        else:
            with patch.object(fixtures,'proposal',return_value=content):f.send()
        obj=self.routes.ToolWorkflow(f.root/'route',route=route,host=f.host,bridge=f.bridge,
                                    executor=Executor(f.intent,f.tool))
        self.addCleanup(obj.close)
        return f,obj

    def test_abstention_is_admitted_without_fabricated_effect(self):
        content=fixtures.proposal(decision='abstain',product=None,quantity=None,expected_version=None,
                                  reason='insufficient_stock')
        for route in ('dohaa','simple'):
            with self.subTest(route=route):
                f,obj=self.separate(route,content)
                self.assertEqual(obj.decide('op-1')['state'],'abstained')
                self.assertFalse(obj.execute('op-1')['effect_applied'])
                self.assertIsNone(f.tool.lookup('op-1'))
                f.intent.prepare(Operation('other','task-a','other-step','reserve','widget',1,0))
                with self.assertRaises(Conflict):obj.recover('op-1')
                self.assertIsNotNone(f.intent.pending('task-a'))

    def test_invalid_native_answer_never_enters_controller(self):
        f,obj=self.separate(content='{}')
        with patch.object(DohaaController,'run',side_effect=AssertionError('invalid proposal')):
            self.assertEqual(obj.decide('op-1')['state'],'invalid')
        self.assertEqual(obj.recover('op-1')['state'],'invalid')
        self.assertIsNone(f.tool.lookup('op-1'))

    def test_simple_route_altered_proposal_cannot_execute(self):
        f,obj=self.separate('simple');original=self.routes._VerifiedRuntime.propose
        def altered(runtime,contract,feedback=()):
            data=original(runtime,contract,feedback).to_dict()
            data['result']['tool_proposal']['expected_version']=9
            return Proposal.from_dict(data)
        with patch.object(self.routes._VerifiedRuntime,'propose',altered):
            self.assertEqual(obj.decide('op-1')['state'],'denied')
        self.assertFalse(obj.execute('op-1')['effect_applied']);self.assertIsNone(f.tool.lookup('op-1'))

    def test_unrelated_pending_task_does_not_block_original(self):
        self.f.intent.prepare(Operation('other','task-b','other-step','reserve','widget',1,0))
        self.workflow.decide('op-1');self.workflow.execute('op-1')
        self.assertIsNotNone(self.f.intent.pending('task-b'))

    def test_effect_created_outside_workflow_cannot_be_adopted(self):
        self.workflow.decide('op-1')
        self.executor.execute(self.f.host.bound_proposal('op-1').operation)
        with self.assertRaises(Conflict):self.workflow.execute('op-1')
        with self.assertRaises(Conflict):self.workflow.recover('op-1')

    def test_corrupt_decision_and_changed_source_fail_closed(self):
        self.workflow.decide('op-1')
        with patch.object(self.routes,'route_sha256',return_value='f'*64):
            with self.assertRaises(Conflict):self.workflow.execute('op-1')
        with self.workflow._transaction() as db:
            db.execute("UPDATE decisions SET payload='{}'")
        with self.assertRaises(Conflict):self.workflow.recover('op-1')

    def test_concurrent_coordinator_cannot_execute_same_route(self):
        self.workflow.decide('op-1')
        with self.workflow._exclusive():
            with self.assertRaises(Conflict):self.workflow.execute('op-1')
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def crash(self, stage, *, expect_effect=False):
        self.workflow.close()
        pid=os.fork()
        if pid==0:
            try:
                obj=self.open()
                obj.fault=lambda where:os._exit(28) if where==stage else None
                self.executor.fault=lambda where:os._exit(28) if stage=='tool_effect' and where=='after_effect' else None
                obj.decide('op-1')
                if stage in ('execution_intent','effect_observed','tool_effect'):obj.execute('op-1')
                os._exit(29)
            except BaseException:os._exit(30)
        _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),28)
        obj=self.open()
        with patch.object(DohaaController,'run',side_effect=AssertionError('controller replay')):
            if stage=='decision_intent':
                with self.assertRaises(Conflict):obj.recover('op-1')
                return
            row=obj.recover('op-1')
        self.assertEqual(row['effect_applied'],expect_effect)
        if not expect_effect:row=obj.execute('op-1')
        self.assertTrue(row['effect_applied']);self.assertEqual(self.f.tool.inventory('widget')['available'],6)
        self.assertEqual(self.f.sends,1)

    def test_crash_before_controller_remains_unresolved(self):self.crash('decision_intent')
    def test_crash_after_controller_recovers_real_ledger(self):self.crash('controller_finished')
    def test_crash_after_decision_does_not_auto_execute(self):self.crash('decision_committed')
    def test_crash_after_execution_intent_recovers_original_operation(self):self.crash('execution_intent',expect_effect=True)
    def test_crash_after_committed_tool_effect_is_not_duplicated(self):self.crash('tool_effect',expect_effect=True)
    def test_crash_after_observed_effect_preserves_final_outcome(self):self.crash('effect_observed',expect_effect=True)
