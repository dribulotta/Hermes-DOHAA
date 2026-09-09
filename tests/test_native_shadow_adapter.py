import base64
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hermes_dohaa.learning import collection, shadow
from hermes_dohaa.learning import native_prompt as n

encode, digest = shadow._canonical, shadow._hash


def policy():
    return {'schema_version': 'hermes-native-shadow-policy/1.0', 'native_commit': 'a' * 40,
        'bridge_sha256': n.native_bridge_sha256(), 'model': 'synthetic-unit-model',
        'endpoint': 'http://127.0.0.1:12345/v1', 'reasoning_effort': 'none',
        'seed': 42, 'temperature': 0.0, 'top_p': 1.0, 'max_tokens': 512,
        'request_timeout_seconds': 2, 'worker_timeout_seconds': 3, 'request_limit': 4,
        'worker_uid': 65534, 'worker_gid': 65534, 'exclusive_backend': True}


def request(policy_sha='c' * 64):
    return {'schema_version': 'hermes-shadow-request/1.0', 'request_id': 'd' * 64,
        'input': 'Exact public request', 'prompt': 'Proposed prompt text',
        'input_sha256': digest(b'Exact public request'), 'prompt_sha256': digest(b'Proposed prompt text'),
        'execution_policy_sha256': policy_sha}


def wire(logical=None, p=None):
    logical, p = logical or request(), p or policy()
    return {**{key: p[key] for key in ('model', 'reasoning_effort', 'seed', 'temperature', 'top_p', 'max_tokens')},
        'stream': True, 'stream_options': {'include_usage': True},
        'messages': [{'role': 'system', 'content': 'Pinned native prefix\n' + n.prompt_frame(logical)},
                     {'role': 'user', 'content': logical['input']}]}


def response(content='{"result":{"answer":7},"actions":[]}', finish='stop'):
    return {'model': 'synthetic-unit-model', 'choices': [
        {'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': finish}]}


def sse(content='answer', finish='stop', done=True):
    first = {'model': 'synthetic-unit-model', 'choices': [{'index': 0,
        'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]}
    last = {'model': 'synthetic-unit-model', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}
    return b'data: ' + encode(first) + b'\n\ndata: ' + encode(last) + b'\n\n' + (b'data: [DONE]\n\n' if done else b'')


class NativePolicyTests(unittest.TestCase):
    def test_policy_binds_both_bridge_files_and_validates_supported_configuration(self):
        p = policy()
        self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
        self.assertEqual(n.native_adapter_sha256(), collection.adapter_source_sha256(object.__new__(n.NativePromptAdapter)))

    def test_identity_budgets_exclusivity_and_unknown_fields_fail_closed(self):
        for key, value in (('worker_uid', 0), ('worker_gid', True), ('seed', True),
            ('request_limit', 1), ('max_tokens', 0), ('request_timeout_seconds', 0),
            ('worker_timeout_seconds', 2), ('exclusive_backend', False), ('native_commit', 'x' * 40),
            ('bridge_sha256', 'f' * 64), ('reasoning_effort', 'unreviewed'), ('top_p', 0),
            ('temperature', True), ('undeclared', 'field')):
            p = policy()
            p[key] = value
            with self.subTest(key=key), self.assertRaises((n.NativePromptError, shadow.ShadowError)):
                n.validate_native_policy(encode(p), digest(encode(p)))

    def test_endpoint_rejects_credentials_queries_dns_and_wrong_routes(self):
        for url in ('http://localhost:12345/v1', 'http://user:pass@127.0.0.1:12345/v1',
                    'http://127.0.0.1/v1', 'http://127.0.0.1:12345/v1?target=other',
                    'http://127.0.0.1:12345/v1#fragment', 'file:///v1',
                    'http://127.0.0.1:12345/v1/other'):
            p = policy()
            p['endpoint'] = url
            with self.subTest(url=url), self.assertRaises(n.NativePromptError):
                n.validate_native_policy(encode(p), digest(encode(p)))

    def test_request_substitution_and_authority_fields_rejected(self):
        raw = request()
        self.assertEqual(n.validate_request(encode(raw), 'c' * 64), raw)
        for field, value in (('prompt', 'changed'), ('input', 'changed'),
                             ('execution_policy_sha256', 'f' * 64), ('oracle', 7), ('request_id', 'invalid')):
            altered = dict(raw, **{field: value})
            with self.subTest(field=field), self.assertRaises((n.NativePromptError, shadow.ShadowError)):
                n.validate_request(encode(altered), 'c' * 64)

    def test_wire_matches_parameters_and_exact_single_public_message(self):
        raw = wire()
        self.assertEqual(n.validate_wire_request(encode(raw), request(), policy()), raw)
        for key, value in (('model', 'different'), ('seed', True), ('max_tokens', 999),
            ('reasoning_effort', 'medium'), ('temperature', 0), ('n', 2),
            ('tools', [{'type': 'function'}]), ('tool_choice', 'required'), ('stream', 1)):
            altered = dict(raw, **{key: value})
            with self.subTest(key=key), self.assertRaises(n.NativePromptError):
                n.validate_wire_request(encode(altered), request(), policy())

    def test_prompt_in_wrong_role_partial_input_duplicate_user_and_history_rejected(self):
        variants = []
        raw = wire()
        raw['messages'][1]['content'] += ' extra'
        variants.append(raw)
        raw = wire()
        raw['messages'][0]['content'] = request()['prompt']
        variants.append(raw)
        raw = wire()
        raw['messages'].append({'role': 'user', 'content': request()['input']})
        variants.append(raw)
        raw = wire()
        raw['messages'].insert(1, {'role': 'assistant', 'content': 'prior output'})
        variants.append(raw)
        raw = wire()
        raw['messages'][0]['content'] += n.prompt_frame(request())
        variants.append(raw)
        for raw in variants:
            with self.assertRaises(n.NativePromptError):
                n.validate_wire_request(encode(raw), request(), policy())

    def test_native_compatibility_think_flag_cannot_contradict_reasoning_policy(self):
        raw = wire()
        raw['think'] = False
        n.validate_wire_request(encode(raw), request(), policy())
        for value in (True, 0, 'false'):
            raw['think'] = value
            with self.assertRaises(n.NativePromptError):
                n.validate_wire_request(encode(raw), request(), policy())
        p = policy(); p['reasoning_effort'] = 'medium'
        raw = wire(p=p); raw['think'] = True
        n.validate_wire_request(encode(raw), request(), p)


class WireResponseTests(unittest.TestCase):
    def test_complete_json_and_sse_keep_exact_visible_content(self):
        for data in (encode(response(' exact text ')), sse(' exact text ')):
            parsed = n.parse_wire_response(data, 'synthetic-unit-model')
            self.assertEqual(parsed, {'content': ' exact text ', 'finish_reason': 'stop', 'reasoning_characters': 0})

    def test_length_is_terminal_and_preserves_partial_text_for_failed_budget(self):
        self.assertEqual(n.parse_wire_response(sse('partial', 'length'), 'synthetic-unit-model')['finish_reason'], 'length')

    def test_reasoning_is_counted_separately_without_exposing_text(self):
        raw = response('answer')
        raw['choices'][0]['message']['reasoning_content'] = 'PRIVATE internal text'
        parsed = n.parse_wire_response(encode(raw), 'synthetic-unit-model')
        self.assertEqual(parsed['reasoning_characters'], 21)
        self.assertNotIn('PRIVATE', json.dumps(parsed))

    def test_incomplete_stream_missing_finish_and_data_after_done_are_rejected(self):
        raw = response()
        raw['choices'][0]['finish_reason'] = None
        for data in (sse(done=False), sse() + b'data: {}\n', encode(raw), b'data: [DONE]\n'):
            with self.assertRaises((n.NativePromptError, shadow.ShadowError)):
                n.parse_wire_response(data, 'synthetic-unit-model')

    def test_model_identity_multiple_choices_boolean_index_and_tool_calls_rejected(self):
        variants = []
        raw = response(); raw['model'] = 'substituted'; variants.append(raw)
        raw = response(); raw['choices'].append(copy.deepcopy(raw['choices'][0])); variants.append(raw)
        raw = response(); raw['choices'][0]['index'] = False; variants.append(raw)
        raw = response(); raw['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'forbidden'}}]; variants.append(raw)
        raw = response(); raw['choices'][0]['message']['role'] = 'user'; variants.append(raw)
        for raw in variants:
            with self.assertRaises(n.NativePromptError):
                n.parse_wire_response(encode(raw), 'synthetic-unit-model')

    def test_duplicate_keys_nonfinite_unicode_oversize_and_unexpected_finish_rejected(self):
        for data in (b'{"model":"a","model":"b"}', b'{"x":NaN}', b'\xff', b'x' * (n.MAX_RESPONSE_WIRE+1),
                     encode(response(finish='tool_calls'))):
            with self.assertRaises((n.NativePromptError, shadow.ShadowError)):
                n.parse_wire_response(data, 'synthetic-unit-model')

    def test_duplicate_terminal_and_content_after_terminal_rejected(self):
        first = sse().split(b'data: [DONE]')[0]
        late = {'model': 'synthetic-unit-model', 'choices': [{'index': 0, 'delta': {'content': 'late'}, 'finish_reason': None}]}
        for suffix in (b'data: ' + encode(late) + b'\n\n', first):
            with self.assertRaises(n.NativePromptError):
                n.parse_wire_response(first + suffix + b'data: [DONE]\n', 'synthetic-unit-model')


class NativeLifecycleTests(unittest.TestCase):
    def adapter(self, directory):
        p = policy()
        data = encode(p)
        cp = encode(collection.create_collection_policy(adapter_sha256=n.native_adapter_sha256(),
            runtime_policy_sha256=digest(data), result_fields={'answer': 'integer'}))
        result = n.NativePromptAdapter(policy_bytes=data, expected_policy_sha256=digest(data),
            collection_policy_bytes=cp, native_source=Path(directory), python=Path('/synthetic/python'),
            evidence_dir=Path(directory) / 'evidence', api_key='PRIVATE synthetic credential')
        return result

    def test_wrong_collection_policy_cannot_construct_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            p = policy(); data = encode(p)
            cp = encode(collection.create_collection_policy(adapter_sha256='f' * 64,
                runtime_policy_sha256=digest(data), result_fields={'answer': 'integer'}))
            with self.assertRaises(n.NativePromptError):
                n.NativePromptAdapter(policy_bytes=data, expected_policy_sha256=digest(data), collection_policy_bytes=cp,
                    native_source=Path(directory), python=Path('/synthetic/python'), evidence_dir=Path(directory)/'new', api_key='private')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unknown_or_closed_state_never_unloads(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter._http = Mock()
            for state in ('new', 'uncertain', 'closed', 'closing'):
                adapter.state = state
                with self.assertRaises(n.NativePromptError):
                    adapter.finish()
            adapter._http.assert_not_called()

    def test_runtime_policy_cannot_be_changed_through_public_mapping_or_property(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            with self.assertRaises(TypeError):
                adapter.policy['max_tokens'] = 9000
            with self.assertRaises(AttributeError):
                adapter.policy = policy()

    def test_unload_uses_exact_owned_instance_and_verifies_empty_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter.state, adapter.owned = 'idle', {'instance-owned'}
            adapter._catalog = Mock(side_effect=[{'instance-owned': adapter.policy['model']}, {}])
            adapter._http = Mock(return_value={'success': True})
            self.assertEqual(json.loads(adapter.finish()), collection._LIFECYCLE)
            adapter._http.assert_called_once_with('http://127.0.0.1:12345/api/v1/models/unload', {'instance_id': 'instance-owned'})
            self.assertEqual(adapter.state, 'closed')

    def test_foreign_or_replaced_instance_prevents_any_unload(self):
        for catalog in ({'foreign': 'other'}, {'replacement': 'synthetic-unit-model'}, {}):
            with tempfile.TemporaryDirectory() as directory:
                adapter = self.adapter(directory)
                adapter.state, adapter.owned = 'idle', {'original'}
                adapter._catalog, adapter._http = Mock(return_value=catalog), Mock()
                with self.assertRaises(n.NativePromptError):
                    adapter.finish()
                adapter._http.assert_not_called()

    def test_unload_acknowledgment_alone_does_not_confirm_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter.state, adapter.owned = 'idle', {'owned'}
            adapter._catalog = Mock(return_value={'owned': adapter.policy['model']})
            adapter._http = Mock(return_value={'success': True})
            with patch.object(n.time, 'sleep'), self.assertRaisesRegex(n.NativePromptError, 'unload_unverified'):
                adapter.finish()
            self.assertEqual(adapter.state, 'closing')
            self.assertEqual(adapter._http.call_count, 1)

    def test_changed_residency_or_request_budget_stops_before_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter.state = 'idle'
            adapter._catalog = Mock(return_value={'foreign': 'another model'})
            with patch.object(n.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(n.NativePromptError, 'residency_changed'):
                    adapter.generate(encode(request(adapter.collection_sha256)))
                adapter.calls = adapter.policy['request_limit']
                with self.assertRaisesRegex(n.NativePromptError, 'not_ready'):
                    adapter.generate(encode(request(adapter.collection_sha256)))
                spawn.assert_not_called()

    def test_catalog_requires_unambiguous_instances_and_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            for raw in ({'models': []}, {'models': [{'key': adapter.policy['model'], 'type': 'llm',
                'loaded_instances': [{'id': 'same'}, {'id': 'same'}]}]}, {'models': 'invalid'}):
                adapter._http = Mock(return_value=raw)
                with self.assertRaises(n.NativePromptError):
                    adapter._catalog()

    @unittest.skipUnless(os.name == 'posix', 'POSIX subprocess contract')
    def test_subprocess_receives_no_inherited_secrets_and_profile_is_sealed(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter.state = 'idle'
            adapter.worker_root = Path(directory)
            adapter.evidence_dir.mkdir()
            adapter._catalog = Mock(side_effect=[{}, {'owned': adapter.policy['model']}])
            logical = request(adapter.collection_sha256)
            data = encode(logical)
            called = {}
            def spawn(argv, **kwargs):
                called.update(argv=argv, kwargs=kwargs)
                child = Mock(returncode=0)
                def communicate(payload, timeout):
                    supplied = json.loads(payload)
                    self.assertEqual(set(supplied), {'policy', 'runtime_policy_sha256', 'collection_policy_sha256',
                        'request', 'request_sha256', 'profile', 'api_key', 'progress_fd'})
                    self.assertEqual(supplied['request'], logical)
                    trace = {'request_sha256': digest(data), 'uid': 65534, 'gid': 65534,
                        'runtime_policy_sha256': adapter.runtime_policy_sha256, 'bridge_sha256': adapter.policy['bridge_sha256'],
                        'identity_isolated': True, 'agent_class': 'AIAgent', 'profile_passed': True,
                        'server_finished': True, 'actual_requests': 1, 'content_matches_wire': True,
                        'native_completed': True, 'denied_continuations': 0,
                        'wire_request_base64': base64.b64encode(encode(wire(logical, adapter.policy))).decode(),
                        'wire_response_base64': base64.b64encode(encode(response())).decode()}
                    kwargs['stdout'].write(encode(trace))
                child.communicate.side_effect = communicate
                return child
            with patch.dict(os.environ, {'ORACLE_PRIVATE_TEST': 'secret parent value'}), \
                    patch.object(n.subprocess, 'Popen', side_effect=spawn), \
                    patch.object(n.os, 'chown') as chown:
                result = json.loads(adapter.generate(data))
            self.assertEqual(result['status'], 'completed')
            self.assertNotIn('PRIVATE', str(called['argv']))
            self.assertNotIn('ORACLE_PRIVATE_TEST', called['kwargs']['env'])
            self.assertNotIn('api_key', called['kwargs']['env'])
            self.assertIn('-P', called['argv'])
            self.assertEqual(called['kwargs']['env']['PYTHONNOUSERSITE'], '1')
            self.assertEqual(called['kwargs']['extra_groups'], [])
            self.assertTrue(called['kwargs']['close_fds'])
            chown.assert_any_call(Path(directory)/'profile-0', 0, 0, follow_symlinks=False)
            self.assertEqual((Path(directory)/'profile-0').stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == 'posix', 'POSIX subprocess contract')
    def test_worker_timeout_kills_only_worker_and_retains_uncertain_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = self.adapter(directory)
            adapter.state, adapter.worker_root = 'idle', Path(directory)
            adapter.evidence_dir.mkdir()
            adapter._catalog = Mock(return_value={})
            child = Mock()
            child.communicate.side_effect = subprocess.TimeoutExpired('worker', 3)
            with patch.object(n.subprocess, 'Popen', return_value=child), patch.object(n.os, 'chown'):
                with self.assertRaisesRegex(n.NativePromptError, 'worker_timeout'):
                    adapter.generate(encode(request(adapter.collection_sha256)))
            child.kill.assert_called_once()
            self.assertEqual(adapter.state, 'uncertain')
            with self.assertRaisesRegex(n.NativePromptError, 'completion_unknown'):
                adapter.finish()


if __name__ == '__main__':
    unittest.main()
