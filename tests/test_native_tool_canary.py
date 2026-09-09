"""Synthetic collector regression cases; not private live-canary questions."""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_dohaa.learning import native_prompt as native, shadow
from tools.controlled_tools import Conflict, Executor
from tools.native_model_residency import ResidencyError
import test_native_tool_evidence as evidence_fixtures


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged collector boundary')
class CanaryTests(unittest.TestCase):
    def setUp(self):
        from tools.native_tool_canary import CanaryCollector, collector_sha256, observed_state
        self.Collector = CanaryCollector
        self.fixture = evidence_fixtures.ToolEvidenceTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.executor = Executor(f.intent, f.tool)
        expected = {'inventory': [{'product': 'widget', 'available': 6, 'version': 1}],
                    'reservations': [{'id': 'op-1', 'task': 'task-a', 'product': 'widget', 'quantity': 4, 'active': 1}],
                    'effect_receipts': 1, 'completed_intents': 1, 'pending_intents': 0}
        self.plan = {'schema_version': 'hermes-native-tool-canary/1.0',
            'collector_sha256': collector_sha256(), 'runtime_policy_sha256': f.ps,
            'collection_policy_sha256': shadow._hash(f.cp),
            'initial_state_sha256': shadow._hash(shadow._canonical(observed_state(f.tool, f.intent))),
            'minimum_correct': 1, 'cases': [{'case_id': 'synthetic-case', 'operation_id': 'op-1',
                'step_sha256': f.host.snapshot('op-1')['step_sha256'],
                'request_base64': base64.b64encode(f.raw).decode(),
                'expected_host_state': 'applied', 'expected_reason': 'applied',
                'expected_effect_code': 'applied', 'expected_state': expected}]}
        self.collector = self.open()

    def open(self, data=None):
        f = self.fixture
        raw = shadow._canonical(self.plan) if data is None else data
        return self.Collector(f.root/'canary', plan_bytes=raw, expected_sha256=shadow._hash(raw),
            host=f.host, bridge=f.bridge, adapter=f.adapter, residency=f.lease, executor=self.executor)

    def run_one(self):
        with patch.object(native.subprocess, 'Popen', side_effect=self.fixture.spawn):
            return self.collector.run_next()

    def test_actual_effect_and_final_state_required_for_correctness(self):
        row = self.run_one()
        self.assertTrue(row['correct'])
        self.assertEqual(self.fixture.tool.inventory('widget')['available'], 6)
        summary = self.collector.finish()
        self.assertEqual((summary['scheduled'], summary['terminal_receipts'], summary['correct']), (1,1,1))
        self.assertTrue(summary['criterion_passed']); self.assertTrue(summary['exact_unload'])
        self.assertIsNone(self.collector.run_next())

    def test_proposal_acceptance_is_not_an_effect(self):
        with patch.object(self.executor, 'execute', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): self.run_one()
        self.assertEqual(self.fixture.host.snapshot('op-1')['state'], 'proposed')
        summary = self.collector.summary()
        self.assertEqual(summary['correct'], 0); self.assertEqual(summary['completed_cases'], 0)
        self.assertFalse(summary['criterion_passed'])

    def test_unknown_terminal_blocks_resend_replacement_and_unload(self):
        self.fixture.trace_change = lambda trace: trace.update(server_finished=False)
        with self.assertRaises(native.NativePromptError): self.run_one()
        for action in (self.collector.run_next, self.open().run_next, self.collector.finish):
            with self.assertRaises((Conflict, ResidencyError)): action()
        self.assertEqual(self.collector.summary()['missing_terminals'], 1)
        self.assertEqual(self.fixture.sends, 1)

    def test_restart_after_committed_effect_recovers_without_resend(self):
        self.executor.fault = lambda stage: (_ for _ in ()).throw(RuntimeError('crash')) if stage == 'after_effect' else None
        with self.assertRaises(RuntimeError): self.run_one()
        self.executor.fault = lambda _: None
        row = self.open().recover_current()
        self.assertTrue(row['correct'])
        self.assertEqual(self.fixture.sends, 1)
        self.assertEqual(self.fixture.tool.inventory('widget')['available'], 6)

    def test_process_crash_before_dispatch_stays_unresolved(self):
        pid = os.fork()
        if pid == 0:
            self.collector.fault = lambda stage: os._exit(25) if stage == 'attempt_committed' else None
            self.run_one(); os._exit(26)
        _, status = os.waitpid(pid,0); self.assertEqual(os.waitstatus_to_exitcode(status),25)
        reopened = self.open()
        with self.assertRaises(Conflict): reopened.run_next()
        with self.assertRaises(Conflict): reopened.recover_current()
        self.assertEqual(self.fixture.host.snapshot('op-1')['state'], 'allocated')

    def test_process_crash_after_effect_preserves_single_reservation(self):
        pid = os.fork()
        if pid == 0:
            self.executor.fault = lambda stage: os._exit(25) if stage == 'after_effect' else None
            self.run_one(); os._exit(26)
        _, status = os.waitpid(pid,0); self.assertEqual(os.waitstatus_to_exitcode(status),25)
        self.assertTrue(self.open().recover_current()['correct'])
        self.assertEqual(self.fixture.tool.inventory('widget')['available'], 6)

    def test_reopened_pending_collector_never_sends(self):
        with self.assertRaises(Conflict): self.open().run_next()
        self.assertEqual(self.fixture.sends, 0)

    def test_mutated_manifest_and_step_fail_before_generation(self):
        changed = copy.deepcopy(self.plan); changed['minimum_correct'] = 0
        with self.assertRaises((Conflict, ValueError)): self.open(shadow._canonical(changed))
        with sqlite3.connect(self.collector.path) as db: db.execute("UPDATE meta SET value='changed' WHERE key='manifest'")
        with self.assertRaises(Conflict): self.run_one()
        self.assertEqual(self.fixture.sends,0)

    def test_wrong_expected_inventory_does_not_count_as_success(self):
        self.collector = None
        # A separate preregistered plan, before any send, for this synthetic test.
        self.plan['cases'][0]['expected_state']['inventory'][0]['available'] = 5
        f=self.fixture
        self.collector=self.Collector(f.root/'different-plan', plan_bytes=shadow._canonical(self.plan),
            expected_sha256=shadow._hash(shadow._canonical(self.plan)), host=f.host, bridge=f.bridge,
            adapter=f.adapter, residency=f.lease, executor=self.executor)
        self.assertFalse(self.run_one()['correct'])
        self.assertFalse(self.collector.finish()['criterion_passed'])

    def test_invalid_proposal_is_terminal_failure_without_effect(self):
        from test_native_shadow_adapter import response, encode
        self.fixture.trace_change=lambda trace: trace.update(wire_response_base64=base64.b64encode(encode(response('{}'))).decode())
        row = self.run_one()
        self.assertEqual(row['host_state'],'invalid'); self.assertFalse(row['correct'])
        self.assertEqual(self.fixture.tool.inventory('widget')['available'],10)
        self.assertTrue(self.collector.finish()['exact_unload'])

    def test_no_second_unload_after_closed_restart(self):
        self.run_one(); self.collector.finish()
        with patch.object(self.fixture.lease, 'unload', side_effect=AssertionError('second unload')):
            self.assertTrue(self.open().finish()['exact_unload'])

    def test_changed_final_effects_invalidate_report(self):
        self.run_one()
        with self.fixture.tool.transaction() as db: db.execute("UPDATE inventory SET available=9")
        with self.assertRaises(Conflict): self.collector.summary()

    def test_live_lock_prevents_parallel_send(self):
        import fcntl
        with (self.fixture.root/'canary'/'collector.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(Conflict): self.run_one()
        self.assertEqual(self.fixture.sends,0)

    def test_in_memory_reclassification_is_blocked(self):
        self.collector.plan['minimum_correct']=0
        with self.assertRaises(Conflict): self.run_one()
        self.assertEqual(self.fixture.sends,0)

    def test_two_cases_preserve_order_and_exact_rejection_code(self):
        from dataclasses import replace
        f=self.fixture
        first=f.host.host_step('op-1')
        f.host.allocate(replace(first,task_id='task-b',step_id='step-2',operation_id='op-2'))
        f.tool.authorize('task-b','widget',7)
        second=copy.deepcopy(f.logical)
        second['request_id']='e'*64;second['input']='Second synthetic public request'
        second['input_sha256']=shadow._hash(second['input'].encode())
        second_raw=shadow._canonical(second)
        case=copy.deepcopy(self.plan['cases'][0])
        case.update(case_id='second',operation_id='op-2',step_sha256=f.host.snapshot('op-2')['step_sha256'],
                    request_base64=base64.b64encode(second_raw).decode(), expected_host_state='rejected',
                    expected_reason='version_mismatch', expected_effect_code='version_mismatch')
        case['expected_state'].update(effect_receipts=2,completed_intents=2)
        plan=copy.deepcopy(self.plan);plan['cases'].append(case);plan['minimum_correct']=2
        self.collector=self.Collector(f.root/'two-cases',plan_bytes=shadow._canonical(plan),
            expected_sha256=shadow._hash(shadow._canonical(plan)),host=f.host,bridge=f.bridge,
            adapter=f.adapter,residency=f.lease,executor=self.executor)
        self.assertTrue(self.run_one()['correct'])
        f.raw=second_raw
        self.assertTrue(self.run_one()['correct'])
        summary=self.collector.finish()
        self.assertEqual((summary['correct'],summary['terminal_receipts'],f.sends),(2,2,2))
        self.assertTrue(summary['criterion_passed'])
