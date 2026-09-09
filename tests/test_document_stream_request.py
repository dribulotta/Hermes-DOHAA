import copy
import json
from pathlib import Path
import tempfile
import unittest

from hermes_dohaa.controller.engine import DohaaController
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from tools.document_stream import DocumentEvent, StreamStore
from tools.document_stream_request import (make_request, native_request, parse_submission,
    task_contract, StreamCitationGate, SingleProposalRuntime)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with StreamStore(self.root/'stream.db', run_id='public-development') as stream:
            stream.append([DocumentEvent('e1', 'source', 1, 1, 'The launch is on Monday.'),
                           DocumentEvent('e2', 'source', 2, 2, 'The launch moves to Friday.')])
            self.observation = stream.observation(2)
        self.task = {'task_id': 'launch-day', 'instruction': 'Report the current launch day.',
                     'fact_keys': ['launch_day']}
        self.request = make_request(self.observation, self.task)
        self.proposal = Proposal({'facts': [{'key': 'launch_day', 'value': 'Friday',
            'evidence': [{'source_id': 'source', 'revision': 2}]}]})

    def tearDown(self):
        self.temp.cleanup()

    def test_whitelisted_request_and_native_bindings(self):
        raw = native_request(self.request, 'a'*64, 'b'*64)
        wire = json.loads(raw)
        import hashlib
        self.assertEqual(hashlib.sha256(wire['input'].encode()).hexdigest(), wire['input_sha256'])
        self.assertEqual(set(json.loads(wire['input'])), {'schema_version', 'observation', 'task'})
        self.assertEqual(wire['execution_policy_sha256'], 'a'*64)

    def test_oracle_fields_rejected_at_every_boundary(self):
        for target in ('observation', 'task', 'event'):
            observation, task = copy.deepcopy(self.observation), copy.deepcopy(self.task)
            node = observation if target == 'observation' else task if target == 'task' else observation['events'][0]
            node['expected'] = {'launch_day': 'Friday'}
            with self.subTest(target=target), self.assertRaises(ValueError):
                make_request(observation, task)
        with self.assertRaises(ValueError):
            native_request(dict(self.request, reference='private'), 'a'*64, 'b'*64)

    def test_future_event_is_not_silently_passed_to_model(self):
        self.observation['at_tick'] = 1
        with self.assertRaises(ValueError):
            make_request(self.observation, self.task)

    def test_ambiguous_revision_and_delivery_rejected(self):
        for duplicate_id in (True, False):
            value = copy.deepcopy(self.observation)
            event = dict(value['events'][1], text='Changed bytes.')
            if not duplicate_id:
                event['event_id'] = 'new-envelope'
            value['events'].append(event)
            with self.assertRaises(ValueError):
                make_request(value, self.task)

    def test_request_is_a_deep_copy(self):
        self.observation['events'][0]['text'] = 'Changed later.'
        self.assertNotEqual(self.request['observation'], self.observation)

    def test_invalid_task_and_hashes(self):
        for keys in ([], ['x', 'x'], [True]):
            with self.assertRaises(ValueError):
                make_request(self.observation, dict(self.task, fact_keys=keys))
        with self.assertRaises(ValueError):
            native_request(self.request, '../path', 'b'*64)

    def test_strict_json_and_empty_side_channels(self):
        self.assertEqual(parse_submission(json.dumps(self.proposal.to_dict())), self.proposal)
        for content in ('{"result":{},"result":{}}', '```json\n{}\n```',
                        '{"result":{"facts":[]},"claims":[],"evidence":[],"requested_actions":[],"extra":1}',
                        '{"result":{"facts":[]},"claims":[],"evidence":[],"requested_actions":["publish"]}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_submission(content)

    def test_gate_accepts_current_but_does_not_claim_semantic_truth(self):
        contract = task_contract(self.request)
        gate = StreamCitationGate()
        self.assertTrue(gate.evaluate(contract, self.proposal).passed)
        wrong = copy.deepcopy(self.proposal.to_dict())
        wrong['result']['facts'][0]['value'] = 'Semantically wrong'
        self.assertTrue(gate.evaluate(contract, Proposal.from_dict(wrong)).passed)

    def test_gate_rejects_stale_unknown_and_empty_citations(self):
        for evidence in ([{'source_id': 'source', 'revision': 1}], [],
                         [{'source_id': 'unknown', 'revision': 2}]):
            wrong = copy.deepcopy(self.proposal.to_dict())
            wrong['result']['facts'][0]['evidence'] = evidence
            self.assertFalse(StreamCitationGate().evaluate(task_contract(self.request), Proposal.from_dict(wrong)).passed)

    def test_gate_rejects_unrequested_key_and_duplicate_fact(self):
        for duplicate in (False, True):
            wrong = copy.deepcopy(self.proposal.to_dict())
            if duplicate:
                wrong['result']['facts'] *= 2
            else:
                wrong['result']['facts'][0]['key'] = 'secret'
            self.assertFalse(StreamCitationGate().evaluate(task_contract(self.request), Proposal.from_dict(wrong)).passed)

    def test_actual_controller_uses_one_proposal_and_valid_ledger(self):
        runtime = SingleProposalRuntime(self.proposal)
        with EvidenceLedger(self.root/'ledger.sqlite3') as ledger:
            result = DohaaController(runtime, [StreamCitationGate()], ledger).run(task_contract(self.request))
            self.assertEqual(result.status.value, 'succeeded')
            self.assertTrue(ledger.verify_chain())
        self.assertEqual(runtime.calls, 1)
        with self.assertRaises(RuntimeError):
            runtime.propose(task_contract(self.request), ())

    def test_actual_controller_rejects_stale_without_retry(self):
        wrong = copy.deepcopy(self.proposal.to_dict())
        wrong['result']['facts'][0]['evidence'][0]['revision'] = 1
        runtime = SingleProposalRuntime(Proposal.from_dict(wrong))
        with EvidenceLedger(self.root/'ledger.sqlite3') as ledger:
            result = DohaaController(runtime, [StreamCitationGate()], ledger).run(task_contract(self.request))
            self.assertNotEqual(result.status.value, 'succeeded')
            self.assertTrue(ledger.verify_chain())
        self.assertEqual(runtime.calls, 1)


if __name__ == '__main__':
    unittest.main()
