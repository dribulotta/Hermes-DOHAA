import base64
import tempfile
import unittest
from unittest.mock import Mock, patch

from hermes_dohaa.learning import native_prompt as n, shadow
import test_native_failure_diagnostics as diagnostic
import test_native_shadow_adapter as fixtures
from test_native_shadow_adapter import encode, policy, request, sse


def large_stream():
    chunk = {'id': 'chatcmpl-' + 's' * 220, 'created': 1, 'model': policy()['model'],
        'object': 'chat.completion.chunk', 'choices': [{'index': 0,
            'delta': {'reasoning_content': 'r'}, 'finish_reason': None}]}
    return (b'data: ' + encode(chunk) + b'\n\n') * 8000 + sse()


class StreamCapacityTests(unittest.TestCase):
    setUp = diagnostic.GuardDiagnosticsTests.setUp
    send = diagnostic.GuardDiagnosticsTests.send

    def test_valid_long_reasoning_stream_is_retained_and_completed(self):
        raw = large_stream()
        self.assertGreater(len(raw), n.MAX_WIRE)
        self.assertLess(len(raw), n.MAX_RESPONSE_WIRE)
        self.transport.return_value = diagnostic.Stream([raw])
        result = self.send()
        self.assertEqual(result.content, raw)
        self.assertEqual(self.guard.response_bytes, raw)
        self.assertTrue(self.guard.server_finished)
        self.assertEqual(self.guard.parsed['reasoning_characters'], 8000)
        self.assertIsNone(self.guard.generation_failure)

    def test_long_stream_without_done_remains_incomplete(self):
        raw = large_stream().split(b'data: [DONE]')[0]
        self.transport.return_value = diagnostic.Stream([raw])
        with self.assertRaisesRegex(n.NativePromptError, 'response_incomplete'):
            self.send()
        self.assertFalse(self.guard.server_finished)
        self.assertEqual(self.guard.response_bytes, raw)
        self.assertEqual(self.guard.generation_failure['code'], 'native_shadow.response_incomplete')

    def test_request_cannot_use_the_larger_response_capacity(self):
        self.request.content = b'x' * (n.MAX_WIRE + 1)
        with self.assertRaisesRegex(n.NativePromptError, 'wire_limit'):
            self.send()
        self.transport.assert_not_called()
        self.assertEqual(self.guard.posts, 0)

    def test_worker_metadata_keeps_the_smaller_bound(self):
        self.request.url = policy()['endpoint'] + '/models'
        self.request.method = 'GET'
        self.transport.return_value = diagnostic.Stream([b'x' * (n.MAX_WIRE + 1)])
        with self.assertRaisesRegex(n.NativePromptError, 'wire_limit'):
            self.send()
        self.assertEqual(self.guard.posts, 0)
        self.assertIsNone(self.guard.generation_failure)

    def test_response_at_limit_parses_but_one_more_byte_is_rejected(self):
        body = sse()
        exact = body + b' ' * (n.MAX_RESPONSE_WIRE - len(body))
        self.assertEqual(n.parse_wire_response(exact, policy()['model'])['content'], 'answer')
        with self.assertRaisesRegex(n.NativePromptError, 'wire_limit'):
            n.parse_wire_response(exact + b' ', policy()['model'])


class TraceCapacityTests(unittest.TestCase):
    def test_long_encoded_trace_fits_reader_and_worker_file_limits(self):
        raw = large_stream()
        value = encode({'wire_response_base64': base64.b64encode(raw).decode(),
                        'wire_request_base64': base64.b64encode(b'q' * n.MAX_WIRE).decode()})
        self.assertGreater(len(value), 4 * 1024 * 1024)
        self.assertLess(len(value), n.MAX_WORKER_TRACE)
        self.assertLessEqual(n.MAX_WORKER_TRACE, shadow.MAX_JSON_BYTES)
        self.assertEqual(shadow._json(value, shadow._hash(value))['wire_response_base64'], base64.b64encode(raw).decode())

    def test_maximum_bodies_fit_serialized_trace_budget(self):
        data = encode({'wire_response_base64': base64.b64encode(b'r' * n.MAX_RESPONSE_WIRE).decode(),
            'wire_request_base64': base64.b64encode(b'q' * n.MAX_WIRE).decode(), 'bounded_metadata': 'm' * 4096})
        self.assertLess(len(data), n.MAX_WORKER_TRACE)
        self.assertLessEqual(48 * n.MAX_WORKER_TRACE, n.MAX_TRACE)

    def test_insufficient_trace_reservation_stops_before_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.NativeLifecycleTests().adapter(directory)
            adapter.state = 'idle'
            adapter._catalog = Mock(return_value={})
            adapter.trace_bytes = n.MAX_TRACE - n.MAX_WORKER_TRACE + 1
            with patch.object(n.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(n.NativePromptError, 'trace_budget'):
                    adapter.generate(encode(request(adapter.collection_sha256)))
                spawn.assert_not_called()
            self.assertEqual(adapter.calls, 0)
            self.assertEqual(adapter.state, 'idle')

    def test_parent_catalog_still_reads_only_the_small_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.NativeLifecycleTests().adapter(directory)
            response = Mock()
            response.read.return_value = b'x' * (n.MAX_WIRE + 1)
            opened = Mock()
            opened.__enter__ = Mock(return_value=response)
            opened.__exit__ = Mock(return_value=False)
            opener = Mock(); opener.open.return_value = opened
            with patch.object(n.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(n.NativePromptError, 'catalog_limit'):
                    adapter._http(policy()['endpoint'] + '/models')
            response.read.assert_called_once_with(n.MAX_WIRE + 1)


if __name__ == '__main__':
    unittest.main()
