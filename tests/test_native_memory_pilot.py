"""Fresh synthetic terminal, scoring and lifecycle controls for the memory pilot."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from hermes_dohaa.learning import collection, native_prompt as n, shadow
from hermes_dohaa.learning.native_reasoning import binary_model_sha256
from hermes_dohaa.learning.native_response_format import document_response_format
from tools import native_memory_pilot as pilot
from tools.document_stream import DocumentEvent as E
from tools.document_stream_request import make_request, native_request
from test_native_shadow_adapter import policy, wire, response, sse, encode, digest


def material():
    original = make_request(dict(run_id='fresh-memory', at_tick=2,
                                events=[E('e1', 'authority', 1, 1, 'answer=31').__dict__]),
                            dict(task_id='memory', instruction='Report answer.', fact_keys=['answer']))
    ref = [dict(key='answer', value='31', evidence=[[['authority', 1]]])]
    content = encode(dict(result=dict(facts=[dict(key='answer', value='31',
                        evidence=[dict(source_id='authority', revision=1)])]),
                        claims=[], evidence=[], requested_actions=[])).decode()
    return original, ref, content


class MemoryScoringTests(unittest.TestCase):
    def test_correct_report_and_shared_public_gate(self):
        original, refs, content = material()
        row = pilot.score_terminal(original, original, refs, dict(status='completed', content=content))
        self.assertTrue(row['correct']); self.assertTrue(row['accepted'])

    def test_omitted_source_does_not_remove_required_facts_from_reference(self):
        original, refs, content = material()
        selected = copy.deepcopy(original); selected['observation']['events'] = []
        empty = encode(dict(result=dict(facts=[]), claims=[], evidence=[], requested_actions=[])).decode()
        row = pilot.score_terminal(original, selected, refs, dict(status='completed', content=empty))
        self.assertFalse(row['correct']); self.assertEqual(row['score']['missing_facts'], 1)
        guessed = pilot.score_terminal(original, selected, refs, dict(status='completed', content=content))
        self.assertFalse(guessed['accepted']); self.assertFalse(guessed['correct'])

    def test_terminal_budget_and_runtime_failures_are_outcomes_not_retries(self):
        original, refs, _ = material()
        for code in ('budget_exhausted', 'runtime_error'):
            row = pilot.score_terminal(original, original, refs, dict(status='failed', error_code=code))
            self.assertFalse(row['correct']); self.assertEqual(row['error_code'], code)

    def test_invalid_and_action_bearing_reports_are_not_accepted(self):
        original, refs, content = material()
        value = json.loads(content); value['requested_actions'] = ['shell.exec']
        for text in ('not JSON', encode(value).decode()):
            row = pilot.score_terminal(original, original, refs, dict(status='completed', content=text))
            self.assertFalse(row['correct']); self.assertFalse(row['accepted'])

    def test_valid_but_wrong_answer_remains_wrong_acceptance(self):
        original, refs, content = material()
        row = pilot.score_terminal(original, original, refs, dict(status='completed', content=content.replace('31', '99')))
        self.assertTrue(row['accepted']); self.assertTrue(row['incorrect_accepted'])

    def test_json_and_complete_sse_usage_and_absent_usage(self):
        counts = dict(prompt_tokens=5, completion_tokens=7, total_tokens=12)
        raw = response(); raw['usage'] = counts
        stream = sse().replace(b'data: [DONE]', b'data: '+encode(dict(model='synthetic-unit-model', choices=[], usage=counts))+b'\n\ndata: [DONE]')
        self.assertEqual(pilot.usage(encode(raw), 'synthetic-unit-model'), counts)
        self.assertEqual(pilot.usage(stream, 'synthetic-unit-model'), counts)
        self.assertIsNone(pilot.usage(sse(), 'synthetic-unit-model'))

    def test_conflicting_or_invalid_usage_is_unmeasured(self):
        raw = response(); raw['usage'] = dict(prompt_tokens=True, completion_tokens=2, total_tokens=3)
        self.assertIsNone(pilot.usage(encode(raw), 'synthetic-unit-model'))
        with self.assertRaises(n.NativePromptError): pilot.usage(sse(done=False), 'synthetic-unit-model')


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'root-owned synthetic collector')
class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.p = dict(policy(), schema_version='hermes-native-shadow-policy/1.2',
                      response_contract='document-stream-proposal/1.0', context_length=8192)
        pb = encode(self.p)
        cb = encode(collection.create_collection_policy(adapter_sha256=n.native_adapter_sha256(),
                     runtime_policy_sha256=digest(pb), result_fields={'result':'object'}))
        self.adapter = n.NativePromptAdapter(policy_bytes=pb, expected_policy_sha256=digest(pb),
            collection_policy_bytes=cb, native_source=self.root, python=Path(sys.executable),
            evidence_dir=self.root/'native', api_key='synthetic')
        original, _, self.content = material()
        self.rb = native_request(original, digest(cb), digest(b'fresh-memory-call'))
        self.instances = []; self.sends = 0; self.unloads = 0; self.change = lambda trace: None
        self.finish = 'stop'
        def start(adapter):
            adapter.evidence_dir.mkdir(mode=0o700)
            adapter.worker_root = self.root/'workers'; adapter.worker_root.mkdir(mode=0o755)
            adapter.state = 'idle'
        self.addCleanup(patch.stopall)
        patch.object(n.NativePromptAdapter, 'start', start).start()
        self.adapter._http = self.http
        self.model_sha = binary_model_sha256(encode(self.catalog()), self.p['model'])

    def catalog(self):
        return dict(models=[dict(key=self.p['model'], type='llm', max_context_length=8192,
            capabilities=dict(reasoning=dict(allowed_options=['off','on'], default='on')),
            loaded_instances=copy.deepcopy(self.instances))])

    def http(self, url, body=None):
        if url.endswith('/load'):
            self.instances[:] = [dict(id='owned', config=dict(context_length=8192))]
            return dict(status='loaded', instance_id='owned', load_config=dict(context_length=8192))
        if url.endswith('/unload'):
            self.assertEqual(body, dict(instance_id='owned')); self.unloads += 1; self.instances.clear()
            return dict(status='unloaded')
        return self.catalog()

    def spawn(self, args, **kw):
        proc = Mock(returncode=0)
        def communicate(data, timeout):
            self.sends += 1; config = json.loads(data); logical = config['request']
            wr = wire(logical, self.p); wr['response_format'] = document_response_format()
            trace = dict(schema_version='hermes-shadow-trace/1.0', request_sha256=digest(self.rb),
                runtime_policy_sha256=self.adapter.runtime_policy_sha256, bridge_sha256=self.p['bridge_sha256'],
                uid=self.p['worker_uid'], gid=self.p['worker_gid'], identity_isolated=True, agent_class='AIAgent',
                profile_passed=True, server_finished=True, native_completed=True, content_matches_wire=True,
                actual_requests=1, denied_continuations=0, wire_request_base64=base64.b64encode(encode(wr)).decode(),
                wire_response_base64=base64.b64encode(sse(self.content, self.finish)).decode())
            self.change(trace); kw['stdout'].write(encode(trace))
        proc.communicate.side_effect = communicate
        return proc

    def call(self):
        with patch.object(n.subprocess, 'Popen', side_effect=self.spawn):
            return pilot.record_call(self.root, self.adapter, self.rb, self.model_sha)

    def test_complete_sse_is_unloaded_before_optional_usage_processing(self):
        result = self.call()
        self.assertEqual(result['status'], 'completed'); self.assertTrue(result['exact_unload'])
        self.assertEqual(self.sends, 1); self.assertEqual(self.unloads, 1); self.assertEqual(self.instances, [])
        self.assertIsNone(result['usage']); self.assertTrue((self.root/'witness.private.json').exists())

    def test_unknown_completion_preserves_lease_without_retry_or_unload(self):
        self.change = lambda t: t.update(server_finished=False)
        result = self.call()
        self.assertEqual(result['status'], 'incomplete'); self.assertFalse(result['exact_unload'])
        self.assertEqual(result['residency_state'], 'generating'); self.assertEqual(self.sends, 1); self.assertEqual(self.unloads, 0)

    def test_known_budget_failure_unloads_exactly_and_counts(self):
        self.finish = 'length'
        result = self.call()
        self.assertEqual(result['terminal_status'], 'failed'); self.assertEqual(result['error_code'], 'budget_exhausted')
        self.assertTrue(result['exact_unload']); self.assertEqual(self.sends, 1)

    def test_second_execution_does_not_generate_or_load_again(self):
        self.call()
        with self.assertRaises((ValueError, n.NativePromptError, FileExistsError)):
            self.call()
        self.assertEqual(self.sends, 1); self.assertEqual(self.unloads, 1)

    def test_model_identity_mismatch_blocks_before_load(self):
        self.model_sha = 'f'*64
        result = self.call()
        self.assertEqual(result['status'], 'incomplete'); self.assertEqual(self.sends, 0)
        self.assertEqual(self.instances, []); self.assertEqual(self.unloads, 0)

    def test_accounting_failure_occurs_after_verified_unload(self):
        with patch.object(pilot, 'usage', side_effect=ValueError('synthetic accounting failure')):
            result = self.call()
        self.assertEqual(result['status'], 'incomplete'); self.assertTrue(result['exact_unload'])
        self.assertEqual(self.sends, 1); self.assertEqual(self.unloads, 1)
