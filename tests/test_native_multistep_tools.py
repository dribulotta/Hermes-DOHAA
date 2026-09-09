"""Actual routed multi-step effects with synthetic transport, no live models."""
import base64
import copy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hermes_dohaa.learning import native_prompt as native, shadow
from hermes_dohaa.controller.engine import DohaaController
from tools.controlled_tools import Conflict, Executor
from tools.controlled_tool_proposal import ReservationRef
from tools.controlled_tool_routes import ToolWorkflow, route_sha256
from tools.native_tool_canary import observed_state
from tools.native_model_residency import ResidencyError
import test_native_tool_evidence as fixtures


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'privileged multi-step evidence')
class MultistepTests(unittest.TestCase):
    def setUp(self):
        from tools import native_multistep_tools as multi
        self.m=multi
        self.f=fixtures.ToolEvidenceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.executor=Executor(self.f.intent,self.f.tool)
        self.workflow=self.open_workflow()
        first=self.f.host.host_step('op-1')
        second=replace(first,step_id='step-2',operation_id='op-2',allowed_kinds=('release',),
            reservations=(ReservationRef('op-1','widget',4),))
        expected=observed_state(self.f.tool,self.f.intent)
        expected.update(inventory=[dict(product='widget',available=6,version=1)],
            reservations=[dict(id='op-1',task='task-a',product='widget',quantity=4,active=1)],
            effect_receipts=1,completed_intents=1)
        second_expected=copy.deepcopy(expected)
        second_expected.update(inventory=[dict(product='widget',available=10,version=2)],
            effect_receipts=2,completed_intents=2)
        second_expected['reservations'][0]['active']=0
        steps=[]
        for i,(step,state) in enumerate(((first,expected),(second,second_expected))):
            request=copy.deepcopy(self.f.logical);request['request_id']=str(i+1)*64
            request['input']='Reserve four widgets' if i==0 else 'Release reservation op-1'
            request['input_sha256']=shadow._hash(request['input'].encode())
            steps.append(dict(host_step=asdict(step),request_base64=base64.b64encode(shadow._canonical(request)).decode(),
                continue_on=['applied'],fault_stage='none',expected=dict(route_state='applied',
                effect_code='applied',host_reason='applied',actual_state=state)))
        self.plan=dict(schema_version='hermes-native-multistep/1.0',collector_sha256=multi.collector_sha256(),
            route_sha256=route_sha256(),route='dohaa',runtime_policy_sha256=self.f.ps,
            collection_policy_sha256=shadow._hash(self.f.cp),task_id='task-a',steps=steps,
            initial_state_sha256=shadow._hash(shadow._canonical(observed_state(self.f.tool,self.f.intent))),
            grants_sha256=multi.grants_sha256(self.f.tool))
        self.responses=[fixtures.proposal(),fixtures.proposal(decision='release',quantity=4,
            expected_version=1,reservation_id='op-1')]
        self.sent=[]

    def open_workflow(self,route='dohaa'):
        obj=ToolWorkflow(self.f.root/'route',route=route,host=self.f.host,bridge=self.f.bridge,executor=self.executor)
        self.addCleanup(obj.close);return obj

    def open(self, plan=None, root=None):
        raw=shadow._canonical(self.plan if plan is None else plan)
        return self.m.MultistepCollector(root or self.f.root/'flow',plan_bytes=raw,
            expected_sha256=shadow._hash(raw),workflow=self.workflow,adapter=self.f.adapter)

    def spawn(self,argv,**kwargs):
        child=self.f.spawn(argv,**kwargs);communicate=child.communicate.side_effect
        def send(data,timeout):
            config=json.loads(data);self.f.raw=shadow._canonical(config['request'])
            self.sent.append(config['request'])
            with patch.object(fixtures,'proposal',return_value=self.responses[len(self.sent)-1]):
                return communicate(data,timeout)
        child.communicate.side_effect=send;return child

    def run_one(self,c):
        with patch.object(native.subprocess,'Popen',side_effect=self.spawn):return c.run_next()

    def test_two_steps_use_actual_controller_and_actual_final_state(self):
        c=self.open();calls=[];original=DohaaController.run
        def run(controller,contract,**kw):calls.append(contract);return original(controller,contract,**kw)
        with patch.object(DohaaController,'run',run):
            self.assertTrue(self.run_one(c)['correct']);self.assertTrue(self.run_one(c)['correct'])
        self.assertEqual(len(calls),2)
        result=c.finish();self.assertTrue(result['flow_passed'])
        self.assertEqual((result['scheduled_steps'],result['terminal_receipts'],result['completed_steps']),(2,2,2))
        self.assertEqual(self.f.tool.inventory('widget')['available'],10)
        self.assertTrue(result['exact_unload']);self.assertEqual(self.f.sends,2)

    def test_second_request_observes_effect_but_no_private_grants_or_oracle(self):
        c=self.open();self.run_one(c);self.run_one(c)
        observed=json.loads(self.sent[1]['input'].split(self.m.OBSERVATION_SEPARATOR)[1])
        self.assertEqual(observed['inventory'][0]['available'],6)
        self.assertEqual(observed['reservations'][0]['id'],'op-1')
        self.assertNotIn('grants',observed);self.assertNotIn('expected',observed)
        self.assertEqual(set(observed),{'inventory','reservations'})

    def test_invalid_step_blocks_suffix_without_removing_denominator(self):
        self.responses[0]='{}';c=self.open();result=self.run_one(c)
        self.assertFalse(result['correct']);self.assertEqual(result['route_state'],'invalid')
        self.assertIsNone(c.run_next());summary=c.finish()
        self.assertEqual((summary['scheduled_steps'],summary['attempted_steps'],summary['blocked_steps']),(2,1,1))
        self.assertFalse(summary['flow_passed']);self.assertTrue(summary['exact_unload'])
        self.assertEqual(self.f.sends,1)

    def test_wrong_reference_changes_scoring_not_controller_or_continuation(self):
        self.plan['steps'][0]['expected']['actual_state']['inventory'][0]['available']=5
        c=self.open();self.assertFalse(self.run_one(c)['correct'])
        self.assertTrue(self.run_one(c)['correct']);self.assertFalse(c.finish()['flow_passed'])
        self.assertEqual(self.f.sends,2)

    def test_reopened_collector_never_generates_pending_suffix(self):
        c=self.open();self.run_one(c);reopened=self.open()
        with self.assertRaises(Conflict):reopened.run_next()
        result=reopened.finish()
        self.assertEqual(result['blocked_steps'],1)
        self.assertEqual(result['blocked_reasons'],['restart_uncollected'])
        self.assertFalse(result['flow_passed']);self.assertEqual(self.f.sends,1)

    def test_fresh_collector_cannot_finalize_unattempted_plan(self):
        c=self.open()
        with self.assertRaises(Conflict):c.finish()
        self.assertEqual(self.f.lease.snapshot()['state'],'ready')

    def test_unknown_completion_never_replays_or_unloads(self):
        c=self.open();self.f.trace_change=lambda trace:trace.update(server_finished=False)
        with self.assertRaises(native.NativePromptError):self.run_one(c)
        for action in (c.run_next,self.open().run_next,self.open().recover_current,self.open().finish):
            with self.assertRaises((Conflict,ResidencyError,native.NativePromptError,ValueError)):action()
        self.assertEqual(self.f.sends,1);self.assertFalse(c.summary()['exact_unload'])

    def test_restart_before_controller_does_not_start_a_decision(self):
        c=self.open();c.fault=lambda s:(_ for _ in ()).throw(RuntimeError('crash')) if s=='terminal_recorded' else None
        with self.assertRaises(RuntimeError):self.run_one(c)
        with patch.object(DohaaController,'run',side_effect=AssertionError('new controller call')):
            with self.assertRaises(Conflict):self.open().recover_current()
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_restart_after_acceptance_does_not_create_execution_intent(self):
        c=self.open();c.fault=lambda s:(_ for _ in ()).throw(RuntimeError('crash')) if s=='decision_recorded' else None
        with self.assertRaises(RuntimeError):self.run_one(c)
        with self.assertRaises(Conflict):self.open().recover_current()
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_known_terminal_can_unload_while_route_stays_explicitly_unresolved(self):
        c=self.open();c.fault=lambda s:(_ for _ in ()).throw(RuntimeError('crash')) if s=='decision_recorded' else None
        with self.assertRaises(RuntimeError):self.run_one(c)
        result=self.open().finish()
        self.assertEqual((result['unresolved_steps'],result['blocked_steps']),(1,1))
        self.assertTrue(result['exact_unload']);self.assertFalse(result['flow_passed'])
        self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_budget_exhaustion_is_counted_and_unloaded_without_effect(self):
        self.f.trace_change=lambda trace:trace.update(denied_continuations=1)
        c=self.open();result=self.run_one(c)
        self.assertEqual(result['route_state'],'generation_failed')
        self.assertEqual(result['host_reason'],'budget_exhausted')
        self.assertTrue(c.finish()['exact_unload']);self.assertIsNone(self.f.tool.lookup('op-1'))

    def test_unreached_declared_fault_cannot_pass_even_with_correct_abstention(self):
        self.plan['steps']=self.plan['steps'][:1];case=self.plan['steps'][0]
        case['fault_stage']='tool_effect'
        case['expected'].update(route_state='abstained',effect_code=None,host_reason='insufficient_stock',
                                actual_state=observed_state(self.f.tool,self.f.intent))
        self.responses[0]=fixtures.proposal(decision='abstain',product=None,quantity=None,expected_version=None,
                                           reason='insufficient_stock')
        c=self.open();self.assertTrue(self.run_one(c)['correct'])
        result=c.finish();self.assertFalse(result['fault_schedule_armed']);self.assertFalse(result['flow_passed'])

    def test_declared_process_fault_recovers_last_effect_without_duplicate(self):
        for stage in ('execution_intent','tool_effect'):
            with self.subTest(stage=stage):
                # Each subcase owns an independent fixture and frozen one-step plan.
                f=MultistepTests();f.setUp();self.addCleanup(f.doCleanups)
                f.plan['steps']=f.plan['steps'][:1];f.plan['steps'][0]['fault_stage']=stage
                f.workflow.close()
                pid=os.fork()
                if pid==0:
                    try:f.workflow=f.open_workflow();f.run_one(f.open());os._exit(87)
                    except BaseException:os._exit(88)
                _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),86)
                f.workflow=f.open_workflow();c=f.open()
                with patch.object(DohaaController,'run',side_effect=AssertionError('controller replay')):
                    self.assertTrue(c.recover_current()['correct'])
                self.assertEqual(f.f.tool.inventory('widget')['available'],6)
                result=c.finish();self.assertTrue(result['flow_passed'])
                self.assertEqual(result['armed_faults'],[stage])
                self.assertNotIn('observed_process_exits',result)

    def test_fork_cannot_inherit_permission_to_send(self):
        c=self.open();pid=os.fork()
        if pid==0:
            try:self.run_one(c)
            except Conflict:os._exit(0)
            os._exit(89)
        _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(c.summary()['attempted_steps'],0)

    def test_manifest_mutation_and_second_collector_binding_blocked(self):
        c=self.open();c.plan['steps'][0]['continue_on']=['invalid']
        with self.assertRaises(Conflict):self.run_one(c)
        with self.assertRaises(Conflict):self.open(root=self.f.root/'replacement')
        self.assertEqual(self.f.sends,0)

    def test_grant_mutation_after_commitment_blocks_new_generation(self):
        c=self.open()
        with self.f.tool.transaction() as db:db.execute('UPDATE grants SET quantity=1')
        with self.assertRaises(Conflict):self.run_one(c)
        self.assertEqual(self.f.sends,0)

    def test_type_equivalent_in_memory_reference_mutation_is_rejected(self):
        c=self.open();c.plan['steps'][0]['expected']['actual_state']['effect_receipts']=True
        with self.assertRaises(Conflict):self.run_one(c)
        self.assertEqual(self.f.sends,0)

    def test_actual_final_state_mutation_invalidates_summary(self):
        c=self.open();self.run_one(c);self.run_one(c)
        with self.f.tool.transaction() as db:db.execute('UPDATE inventory SET available=9')
        with self.assertRaises(Conflict):c.summary()

    def test_second_unload_is_not_issued_after_restart(self):
        c=self.open();self.run_one(c);self.run_one(c);c.finish()
        with patch.object(self.f.lease,'unload',side_effect=AssertionError('duplicate unload')):
            self.assertTrue(self.open().finish()['exact_unload'])

    def test_worker_cannot_read_or_modify_private_plan(self):
        c=self.open()
        code='import sys\nfor p in sys.argv[1:]:\n for m in ("rb","ab"):\n  try:open(p,m).close()\n  except PermissionError:continue\n  raise SystemExit(8)\n'
        result=subprocess.run([sys.executable,'-c',code,str(c.path),str(c.root/'plan.private.json')],
            user=self.f.p['worker_uid'],group=self.f.p['worker_gid'],extra_groups=[],cwd='/tmp')
        self.assertEqual(result.returncode,0)

    def test_simple_route_runs_equivalent_flow_without_controller(self):
        f=fixtures.ToolEvidenceTests();f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;self.executor=Executor(f.intent,f.tool);self.workflow=self.open_workflow('simple')
        self.plan.update(route='simple',grants_sha256=self.m.grants_sha256(f.tool))
        c=self.open()
        with patch.object(DohaaController,'run',side_effect=AssertionError('not DOHAA')):
            self.run_one(c);self.run_one(c)
        self.assertTrue(c.finish()['flow_passed'])
