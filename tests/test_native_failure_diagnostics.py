import base64
import copy
import json
import os
import socket
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from hermes_dohaa.learning import native_prompt as n, native_worker as w
import test_native_shadow_adapter as fixtures
from test_native_shadow_adapter import digest, encode, policy, request, response, sse, wire


class TimeoutException(Exception):
    pass


class Stream:
    def __init__(self, chunks=(), error=None, status=200, close_error=None):
        self.chunks, self.error, self.status_code = chunks, error, status
        self.close_error, self.closed, self.headers = close_error, False, {}

    def iter_bytes(self):
        yield from self.chunks
        if self.error:
            raise self.error

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class GuardDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.transport = Mock()
        fake = types.ModuleType('httpx')
        fake.TimeoutException = TimeoutException
        fake.Response = lambda status, **kwargs: types.SimpleNamespace(status_code=status, **kwargs)
        fake.Client = type('Client', (), {'send': lambda _, req, **kw: self.transport(req, **kw)})
        self.stack.enter_context(patch.dict(sys.modules, {'httpx': fake}))
        self.stack.enter_context(patch.object(socket.socket, 'connect', socket.socket.connect))
        self.stack.enter_context(patch.object(socket.socket, 'connect_ex', socket.socket.connect_ex))
        self.guard = w.Guard(policy(), request(), 'synthetic-credential')
        self.guard.install()
        self.client = fake.Client()
        self.request = types.SimpleNamespace(url=policy()['endpoint'] + '/chat/completions', method='POST',
            content=encode(wire()), headers={'authorization': 'Bearer synthetic-credential'}, extensions={})

    def send(self):
        return self.client.send(self.request)

    def failure(self, code, stage, observed, retained, status=200):
        value = self.guard.generation_failure
        self.assertEqual(value, {'code': 'native_shadow.' + code, 'stage': stage,
            'request_bytes': len(self.request.content), 'response_bytes_observed': observed,
            'response_bytes_retained': retained, 'http_status': status})
        self.assertEqual(n.worker_failure_code({'generation_failure': value}), value['code'])
        self.assertFalse(self.guard.server_finished)
        self.assertEqual(self.guard.posts, 1)
        self.assertEqual(self.transport.call_count, 1)
        self.assertNotIn('private exception', json.dumps(value))

    def test_send_timeout_has_finite_diagnostic_without_exception_text(self):
        self.transport.side_effect = TimeoutException('private exception with token or URL')
        with self.assertRaises(TimeoutException):
            self.send()
        self.failure('transport_timeout', 'send', 0, 0, None)

    def test_send_failure_has_no_invented_response(self):
        self.transport.side_effect = OSError('private exception')
        with self.assertRaises(OSError):
            self.send()
        self.failure('transport_error', 'send', 0, 0, None)

    def test_read_failure_retains_small_prefix_and_first_cause_through_retries(self):
        stream = Stream([b'data: {'], TimeoutException('private exception'), close_error=OSError('close'))
        self.transport.return_value = stream
        with self.assertRaises(TimeoutException):
            self.send()
        original = copy.deepcopy(self.guard.generation_failure)
        for _ in range(5):
            with self.assertRaises(w.BudgetStop):
                self.send()
        with self.assertRaises(w.BudgetStop):
            self.guard.deny_summary()
        self.assertEqual(self.guard.denied, 6)
        self.assertEqual(original, self.guard.generation_failure)
        self.assertTrue(stream.closed)
        self.assertEqual(self.guard.response_bytes, b'data: {')
        self.failure('transport_timeout', 'read', 7, 7)

    def test_generic_read_error_is_distinguished_from_timeout(self):
        self.transport.return_value = Stream([b'x'], OSError('private exception'))
        with self.assertRaises(OSError):
            self.send()
        self.failure('transport_error', 'read', 1, 1)

    def test_capture_limit_retains_exact_bounded_prefix_of_large_chunk(self):
        self.transport.return_value = Stream([b'a' * 3, b'b' * n.MAX_WIRE])
        with self.assertRaisesRegex(n.NativePromptError, 'wire_limit'):
            self.send()
        self.failure('wire_limit', 'read', n.MAX_WIRE + 3, n.MAX_WIRE)
        self.assertEqual(self.guard.response_bytes, b'aaa' + b'b' * (n.MAX_WIRE - 3))

    def test_http_status_preserves_body_as_private_evidence_only(self):
        self.transport.return_value = Stream([b'private exception'], status=503)
        with self.assertRaisesRegex(n.NativePromptError, 'http_status'):
            self.send()
        self.failure('http_status', 'http_status', 17, 17, 503)

    def test_invalid_json_and_incomplete_sse_have_distinct_codes(self):
        for raw, code in ((b'not json', 'response_invalid'), (sse(done=False), 'response_incomplete')):
            with self.subTest(code=code):
                self.guard.posts = 0
                self.guard.response_observed = 0
                self.guard.generation_failure = None
                self.transport.reset_mock()
                self.transport.return_value = Stream([raw])
                with self.assertRaises(Exception):
                    self.send()
                self.failure(code, 'parse', len(raw), len(raw))

    def test_close_failure_does_not_claim_server_finished(self):
        raw = encode(response())
        self.transport.return_value = Stream([raw], close_error=OSError('private exception'))
        with self.assertRaises(OSError):
            self.send()
        self.failure('transport_error', 'close', len(raw), len(raw))

    def test_success_preserves_exact_wire_parameters_and_completion(self):
        raw = sse()
        self.transport.return_value = Stream([raw[:5], raw[5:]])
        result = self.send()
        self.assertEqual(result.content, raw)
        self.assertEqual(self.guard.response_bytes, raw)
        self.assertTrue(self.guard.server_finished)
        self.assertIsNone(self.guard.generation_failure)
        self.assertEqual(self.request.extensions['timeout'], dict.fromkeys(('connect', 'read', 'write', 'pool'), 2))
        self.assertEqual(self.transport.call_args.kwargs, {'stream': True, 'follow_redirects': False})

    def test_foreign_destination_and_payload_mutation_do_not_dispatch(self):
        self.request.url = 'http://127.0.0.2:12345/v1/chat/completions'
        with self.assertRaisesRegex(n.NativePromptError, 'network_destination'):
            self.send()
        self.request.url = policy()['endpoint'] + '/chat/completions'
        altered = wire(); altered['max_tokens'] += 1
        self.request.content = encode(altered)
        with self.assertRaisesRegex(n.NativePromptError, 'wire_parameters'):
            self.send()
        self.transport.assert_not_called()
        self.assertIsNone(self.guard.generation_failure)


def diagnostic():
    return {'code': 'native_shadow.transport_timeout', 'stage': 'read', 'request_bytes': 10,
        'response_bytes_observed': 7, 'response_bytes_retained': 7, 'http_status': 200}


class DiagnosticValidationTests(unittest.TestCase):
    def test_malformed_or_unbounded_diagnostics_never_export_arbitrary_values(self):
        for field, value in (('code', 'private exception'), ('code', []), ('stage', {}),
            ('request_bytes', True), ('response_bytes_observed', -1), ('response_bytes_observed', 2**63),
            ('response_bytes_retained', n.MAX_WIRE + 1), ('response_bytes_retained', 8),
            ('http_status', 'private exception'), ('http_status', True), ('extra', 'private exception')):
            with self.subTest(field=field, value=value):
                bad = {**diagnostic(), field: value}
                self.assertEqual(n.worker_failure_code({'generation_failure': bad}), 'native_shadow.worker_unverified')
        for bad in (None, [], {}, {'code': 'native_shadow.transport_timeout'}):
            self.assertEqual(n.worker_failure_code({'generation_failure': bad}), 'native_shadow.worker_unverified')

    @unittest.skipUnless(os.name == 'posix', 'POSIX subprocess contract')
    def test_bound_failure_propagates_but_cannot_authorize_more_calls_or_unload(self):
        for altered, code in (({}, 'transport_timeout'), ({'request_sha256': 'f'*64}, 'worker_unverified'),
                              ({'profile_passed': False}, 'worker_unverified'),
                              ({'server_finished': True}, 'worker_unverified')):
            with self.subTest(altered=altered), tempfile.TemporaryDirectory() as directory:
                adapter = fixtures.NativeLifecycleTests().adapter(directory)
                adapter.state, adapter.worker_root = 'idle', Path(directory)
                adapter.evidence_dir.mkdir()
                adapter._catalog, adapter._http = Mock(return_value={}), Mock()
                data = encode(request(adapter.collection_sha256))
                trace = {'request_sha256': digest(data), 'uid': 65534, 'gid': 65534,
                    'runtime_policy_sha256': adapter.runtime_policy_sha256,
                    'bridge_sha256': adapter.policy['bridge_sha256'], 'identity_isolated': True,
                    'agent_class': 'AIAgent', 'profile_passed': True, 'server_finished': False,
                    'actual_requests': 1, 'generation_failure': diagnostic(),
                    'wire_response_base64': base64.b64encode(b'data: {').decode(), **altered}
                def spawn(*args, **kwargs):
                    child = Mock(returncode=0)
                    child.communicate.side_effect = lambda *a, **k: kwargs['stdout'].write(encode(trace))
                    return child
                with patch.object(n.subprocess, 'Popen', side_effect=spawn) as process, patch.object(n.os, 'chown'):
                    with self.assertRaises(n.NativePromptError) as raised:
                        adapter.generate(data)
                    self.assertEqual(raised.exception.code, 'native_shadow.' + code)
                    self.assertEqual(adapter.state, 'uncertain')
                    with self.assertRaisesRegex(n.NativePromptError, 'not_ready'):
                        adapter.generate(data)
                    with self.assertRaisesRegex(n.NativePromptError, 'completion_unknown'):
                        adapter.finish()
                    self.assertEqual(process.call_count, 1)
                adapter._http.assert_not_called()
                self.assertEqual(adapter.calls, 1)


if __name__ == '__main__':
    unittest.main()
