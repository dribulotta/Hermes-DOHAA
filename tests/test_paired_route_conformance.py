"""Fresh synthetic pairs in independent stores; never real LLM observations."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.runtime.base import Proposal
from tools import controlled_tool_routes as routes
from tools.controlled_tools import Conflict, Executor
import test_native_tool_evidence as fixtures


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged synthetic pairs')
class PairedRouteConformanceTests(unittest.TestCase):
    def pair(self, content=None):
        content = fixtures.proposal() if content is None else content
        pair = []
        for route in ('dohaa', 'simple'):
            f = fixtures.ToolEvidenceTests(); f.setUp(); self.addCleanup(f.doCleanups)
            with patch.object(fixtures, 'proposal', return_value=content): f.send()
            obj = routes.ToolWorkflow(f.root/'route', route=route, host=f.host,
                                      bridge=f.bridge, executor=Executor(f.intent, f.tool))
            self.addCleanup(obj.close); pair.append((f, obj))
        self.assertNotEqual(pair[0][0].root, pair[1][0].root)
        self.assertEqual(pair[0][0].tool.inventory('widget'), pair[1][0].tool.inventory('widget'))
        return pair

    def test_identical_fresh_proposal_is_fixed_before_route_and_effects_are_independent(self):
        pair = self.pair(); observed = []; original = DohaaController.run
        def record(controller, contract, **kw):
            observed.append(contract.max_attempts); return original(controller, contract, **kw)
        for f, obj in pair: f.host.record_terminal('op-1')
        proposals = [f.host.snapshot('op-1')['proposal_json'] for f, obj in pair]
        self.assertEqual(proposals[0], proposals[1])
        with patch.object(DohaaController, 'run', record):
            for f, obj in pair:
                self.assertEqual(obj.decide('op-1')['state'], 'accepted')
                self.assertEqual(f.sends, 1)
        self.assertEqual(observed, [1])
        pair[0][1].execute('op-1')
        self.assertEqual(pair[1][0].tool.inventory('widget')['available'], 10)
        pair[1][1].execute('op-1')
        for f, obj in pair:
            self.assertEqual(f.tool.inventory('widget')['available'], 6)
            self.assertEqual(f.host.snapshot('op-1')['proposal_json'], proposals[0])
            self.assertEqual(f.sends, 1)

    def test_paired_abstention_has_no_effect_receipt(self):
        content = fixtures.proposal(decision='abstain', product=None, quantity=None,
                                    expected_version=None, reason='insufficient_stock')
        for f, obj in self.pair(content):
            self.assertEqual(obj.decide('op-1')['state'], 'abstained')
            self.assertFalse(obj.execute('op-1')['effect_applied'])
            self.assertIsNone(f.tool.lookup('op-1'))

    def test_paired_substituted_action_is_denied_without_regeneration(self):
        original = routes._VerifiedRuntime.propose
        def alter(runtime, contract, feedback=()):
            data = original(runtime, contract, feedback).to_dict()
            data['requested_actions'] = ['shell.exec']
            return Proposal.from_dict(data)
        for f, obj in self.pair():
            with patch.object(routes._VerifiedRuntime, 'propose', alter):
                self.assertEqual(obj.decide('op-1')['state'], 'denied')
            self.assertFalse(obj.execute('op-1')['effect_applied'])
            self.assertEqual(f.sends, 1)

    def test_paired_corrupt_binding_cannot_authorize_any_effect(self):
        for f, obj in self.pair():
            path = f.adapter.evidence_dir/'worker-0000.json'
            data = json.loads(path.read_bytes()); data['actual_requests'] = 2
            path.write_bytes(fixtures.encode(data))
            with self.assertRaises((Conflict, ValueError, fixtures.n.NativePromptError)):
                obj.decide('op-1')
            self.assertIsNone(f.tool.lookup('op-1')); self.assertEqual(f.sends, 1)

    def test_paired_process_interruptions_recover_without_model_or_effect_replay(self):
        for stage in ('decision_intent', 'controller_finished', 'decision_committed',
                      'execution_intent', 'tool_effect', 'effect_observed'):
            with self.subTest(stage=stage):
                pair = self.pair()
                for f, obj in pair: obj.close()
                for f, closed in pair:
                    pid = os.fork()
                    if pid == 0:
                        try:
                            obj = routes.ToolWorkflow(f.root/'route', route=closed.route,
                                host=f.host, bridge=f.bridge, executor=Executor(f.intent, f.tool))
                            obj.fault = lambda where: os._exit(28) if where == stage else None
                            obj.executor.fault = lambda where: os._exit(28) if stage == 'tool_effect' and where == 'after_effect' else None
                            obj.decide('op-1'); obj.execute('op-1'); os._exit(29)
                        except BaseException: os._exit(30)
                    _, status = os.waitpid(pid, 0)
                    self.assertEqual(os.waitstatus_to_exitcode(status), 28)
                    obj = routes.ToolWorkflow(f.root/'route', route=closed.route,
                        host=f.host, bridge=f.bridge, executor=Executor(f.intent, f.tool))
                    self.addCleanup(obj.close)
                    with patch.object(routes._VerifiedRuntime, 'propose', side_effect=AssertionError('proposal replay')):
                        if stage == 'decision_intent':
                            with self.assertRaises(Conflict): obj.recover('op-1')
                            self.assertIsNone(f.tool.lookup('op-1'))
                            continue
                        obj.recover('op-1'); result = obj.execute('op-1')
                        self.assertTrue(result['effect_applied'])
                        self.assertEqual(f.tool.inventory('widget')['available'], 6)
                        self.assertEqual(f.sends, 1)
