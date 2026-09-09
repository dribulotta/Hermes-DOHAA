"""Bounded diagnostic channel tests: synthetic data only, no model/provider."""
import json
import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hermes_dohaa.learning import native_prompt as n, native_progress as p
import test_native_shadow_adapter as fixtures
from test_native_shadow_adapter import encode, request


def bindings():
    return {'request_sha256':'a'*64, 'runtime_policy_sha256':'b'*64, 'bridge_sha256':'c'*64}


def frame(**changes):
    return {**bindings(), 'schema_version':'hermes-native-progress/1.0', 'sequence':1,
            'stage':'read', 'elapsed_ms':10, 'generation_requests':1,
            'request_bytes':512, 'response_bytes_observed':12, 'response_bytes_retained':12,
            'http_status':200, **changes}


class ProgressValidationTests(unittest.TestCase):
    def test_progress_module_is_part_of_the_bridge_code_binding(self):
        before=n.native_bridge_sha256()
        original=Path.read_bytes
        def changed(path):
            value=original(path)
            return value+b'\n# synthetic code change\n' if path.name=='native_progress.py' else value
        with patch.object(Path,'read_bytes',changed):
            self.assertNotEqual(n.native_bridge_sha256(),before)

    def test_valid_progress_is_finite_and_request_bound(self):
        value=frame()
        self.assertEqual(p.parse_progress(encode(value),bindings(),3000), value)

    def test_malformed_partial_private_or_forged_fields_are_rejected(self):
        for raw in (b'', b'{', b'[]', b'x'*(p.MAX_PACKET_BYTES+1),
                    encode(frame(secret='private-token')),
                    encode(frame(stage=[])), encode(frame(sequence=65)),
                    encode(frame(server_finished=True)), encode(frame(stage='private-token')),
                    encode(frame(request_sha256='f'*64)),
                    encode(frame(runtime_policy_sha256='f'*64)),
                    encode(frame(bridge_sha256='f'*64)),
                    encode(frame(sequence=True)), encode(frame(generation_requests=2)),
                    encode(frame(elapsed_ms=3001)), encode(frame(request_bytes=512*1024+1)),
                    encode(frame(response_bytes_retained=13)), encode(frame(http_status=True)),
                    encode(frame())[:-1]+b',"stage":"send"}'):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                p.parse_progress(raw,bindings(),3000)

    def test_stale_sequences_and_regressing_counters_are_rejected(self):
        before=frame(sequence=2,elapsed_ms=50)
        for after in (frame(sequence=2,elapsed_ms=60), frame(sequence=3,elapsed_ms=49),
                      frame(sequence=3,elapsed_ms=60,generation_requests=0),
                      frame(sequence=3,elapsed_ms=60,response_bytes_observed=11,response_bytes_retained=11)):
            with self.assertRaises(ValueError):
                p.parse_progress(encode(after),bindings(),3000,before)


@unittest.skipUnless(os.name=='posix','POSIX capability channel')
class ProgressChannelTests(unittest.TestCase):
    def test_unavailable_reader_thread_does_not_pass_a_closed_capability(self):
        with patch.object(p.threading.Thread,'start',side_effect=RuntimeError('cannot start reader')):
            with p.ProgressChannel(bindings(),3000) as channel:
                self.assertIsNone(channel.child_fd)
        self.assertEqual(channel.summary()['status'],'unavailable')

    def test_guard_reports_read_progress_and_parse_without_completion_authority(self):
        import test_native_failure_diagnostics as diagnostics
        for failed in (False, True):
            with self.subTest(failed=failed), p.ProgressChannel(bindings(),3000) as channel:
                writer=p.ProgressWriter(os.dup(channel.child_fd),bindings(),3000)
                fixture=diagnostics.GuardDiagnosticsTests()
                fixture.setUp()
                try:
                    fixture.guard.progress=writer
                    raw=b'data: {' if failed else diagnostics.sse()
                    fixture.transport.return_value=diagnostics.Stream([raw],
                        error=diagnostics.TimeoutException('private') if failed else None)
                    if failed:
                        with self.assertRaises(diagnostics.TimeoutException):fixture.send()
                    else:
                        fixture.send()
                    self.assertEqual(fixture.transport.call_count,1)
                finally:
                    fixture.doCleanups()
                    writer.close()
            result=channel.summary()
            self.assertEqual(result['last']['stage'],'read' if failed else 'guard_returned')
            self.assertEqual(result['last']['response_bytes_retained'],len(raw))
            self.assertFalse(result['server_completion_verified'])
            self.assertNotIn('private',json.dumps(result))

    def test_bad_or_unwritable_progress_does_not_override_valid_terminal_trace(self):
        for storage_failure in (False, True):
            with self.subTest(storage_failure=storage_failure), tempfile.TemporaryDirectory() as directory:
                adapter=fixtures.NativeLifecycleTests().adapter(directory)
                adapter.state,adapter.worker_root='idle',Path(directory)
                adapter.evidence_dir.mkdir()
                adapter._catalog=Mock(side_effect=[{}, {'owned':adapter.policy['model']}])
                logical=request(adapter.collection_sha256);data=encode(logical)
                def spawn(*args,**kwargs):
                    sender=socket.socket(fileno=os.dup(kwargs['pass_fds'][0]))
                    child=Mock(returncode=0)
                    def communicate(*args,**ignored):
                        sender.send(b'{"private":"forged partial');sender.close()
                        trace={'request_sha256':fixtures.digest(data),'uid':65534,'gid':65534,
                            'runtime_policy_sha256':adapter.runtime_policy_sha256,
                            'bridge_sha256':adapter.policy['bridge_sha256'],'identity_isolated':True,
                            'agent_class':'AIAgent','profile_passed':True,'server_finished':True,
                            'actual_requests':1,'content_matches_wire':True,'native_completed':True,
                            'denied_continuations':0,
                            'wire_request_base64':base64.b64encode(encode(fixtures.wire(logical,adapter.policy))).decode(),
                            'wire_response_base64':base64.b64encode(encode(fixtures.response())).decode()}
                        kwargs['stdout'].write(encode(trace))
                    child.communicate.side_effect=communicate
                    return child
                original=n.shadow._publish
                def publish(*args):
                    if storage_failure:raise OSError('private storage error')
                    return original(*args)
                with patch.object(n.subprocess,'Popen',side_effect=spawn),patch.object(n.os,'chown'), \
                     patch.object(n.shadow,'_publish',side_effect=publish):
                    result=json.loads(adapter.generate(data))
                self.assertEqual(result['status'],'completed')
                self.assertEqual(adapter.state,'idle')
                self.assertEqual(adapter.last_progress['status'],'storage_unavailable' if storage_failure else 'rejected')
                self.assertNotIn('private',json.dumps(adapter.last_progress))

    def test_progress_storage_reservation_stays_within_existing_trace_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=fixtures.NativeLifecycleTests().adapter(directory)
            adapter.state='idle';adapter._catalog=Mock(return_value={})
            adapter.trace_bytes=n.MAX_TRACE-n.MAX_WORKER_TRACE
            with patch.object(n.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(n.NativePromptError,'trace_budget'):
                    adapter.generate(encode(request(adapter.collection_sha256)))
            spawn.assert_not_called()

    def test_real_child_capability_survives_child_termination(self):
        code = '''import sys,time
sys.path.insert(0,sys.argv[1])
from hermes_dohaa.learning.native_progress import ProgressWriter
w=ProgressWriter(int(sys.argv[2]),{'request_sha256':'a'*64,'runtime_policy_sha256':'b'*64,'bridge_sha256':'c'*64},3000)
w.emit('read',generation_requests=1,request_bytes=512,response_bytes_observed=12,response_bytes_retained=12)
time.sleep(10)
'''
        with p.ProgressChannel(bindings(),3000) as channel:
            process=subprocess.Popen([sys.executable,'-B','-c',code,
                str(Path(p.__file__).resolve().parents[2]),str(channel.child_fd)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(channel.child_fd,))
            channel.close_parent_writer()
            try:
                deadline=time.monotonic()+5
                while channel.summary()['last'] is None and process.poll() is None and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertEqual(channel.summary()['last']['stage'],'read')
            finally:
                process.kill()
                stdout,stderr=process.communicate(timeout=5)
        self.assertEqual(stdout,b'')
        self.assertEqual(stderr,b'')
        self.assertEqual(channel.summary()['status'],'observed')
        self.assertFalse(channel.summary()['server_completion_verified'])

    def test_unavailable_socket_is_optional(self):
        with patch.object(p.socket,'socketpair',side_effect=OSError('private failure')):
            with p.ProgressChannel(bindings(),3000) as channel:
                self.assertIsNone(channel.child_fd)
        self.assertEqual(channel.summary()['status'],'unavailable')

    def test_first_decoded_bytes_are_reported_without_waiting_for_interval(self):
        with p.ProgressChannel(bindings(),3000) as channel:
            writer=p.ProgressWriter(os.dup(channel.child_fd),bindings(),3000)
            with patch.object(p.time,'monotonic',return_value=writer.started):
                writer.emit('read',generation_requests=1,request_bytes=512)
                writer.emit('read',generation_requests=1,request_bytes=512,
                            response_bytes_observed=12,response_bytes_retained=12)
            writer.close()
        self.assertEqual(channel.summary()['last']['response_bytes_observed'],12)

    def test_progress_is_retained_when_child_has_no_terminal_output(self):
        with p.ProgressChannel(bindings(),3000) as channel:
            duplicate=os.dup(channel.child_fd)
            channel.close_parent_writer()
            writer=p.ProgressWriter(duplicate,bindings(),3000)
            writer.emit('send',generation_requests=1,request_bytes=512)
            writer.emit('read',generation_requests=1,request_bytes=512,
                        response_bytes_observed=12,response_bytes_retained=12,http_status=200)
            writer.close()
        summary=channel.summary()
        self.assertEqual(summary['status'],'observed')
        self.assertEqual(summary['last']['stage'],'read')
        self.assertFalse(summary['authoritative'])
        self.assertFalse(summary['server_completion_verified'])
        self.assertLessEqual(len(encode(summary)),p.MAX_PROGRESS_BYTES)

    def test_partial_or_oversized_message_discards_progress_without_echo(self):
        for raw in (b'{"secret":"private',b'x'*(p.MAX_PACKET_BYTES+1)):
            with p.ProgressChannel(bindings(),3000) as channel:
                sender=socket.socket(fileno=os.dup(channel.child_fd))
                sender.send(raw);sender.close()
            result=channel.summary()
            self.assertEqual(result['status'],'rejected')
            self.assertIsNone(result['last'])
            self.assertNotIn('private',json.dumps(result))

    def test_missing_channel_is_optional_and_never_blocks_generation(self):
        writer=p.ProgressWriter(None,bindings(),3000)
        for _ in range(1000):writer.emit('send')
        writer.close()
        with p.ProgressChannel(bindings(),3000) as channel:pass
        self.assertEqual(channel.summary()['status'],'unavailable')

    def test_emission_is_bounded_and_does_not_block_after_receiver_closes(self):
        with p.ProgressChannel(bindings(),3000) as channel:
            writer=p.ProgressWriter(os.dup(channel.child_fd),bindings(),3000)
            for _ in range(500):writer.emit('send')
        self.assertLessEqual(writer.sent,p.MAX_PACKETS)
        started=time.monotonic()
        for _ in range(500):writer.emit('read')
        writer.close()
        self.assertLess(time.monotonic()-started,1)

    def test_timeout_progress_cannot_allow_retry_or_model_unload(self):
        for stage in ('send','read','parse','native_returned'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                adapter=fixtures.NativeLifecycleTests().adapter(directory)
                adapter.state,adapter.worker_root='idle',Path(directory)
                adapter.evidence_dir.mkdir()
                adapter._catalog,adapter._http=Mock(return_value={}),Mock()
                child=Mock()
                def spawn(*args,**kwargs):
                    child_fd=os.dup(kwargs['pass_fds'][0])
                    def communicate(data,timeout):
                        config=json.loads(data)
                        self.assertEqual(kwargs['pass_fds'],(config['progress_fd'],))
                        writer=p.ProgressWriter(child_fd,{k:config[k] if k!='bridge_sha256'
                            else config['policy'][k] for k in bindings()},int(timeout*1000))
                        writer.emit(stage,generation_requests=1,request_bytes=512,
                                    response_bytes_observed=12,response_bytes_retained=12)
                        writer.close()
                        raise subprocess.TimeoutExpired('synthetic-worker',timeout)
                    child.communicate.side_effect=communicate
                    return child
                with patch.object(n.subprocess,'Popen',side_effect=spawn) as popen,patch.object(n.os,'chown'):
                    data=encode(request(adapter.collection_sha256))
                    with self.assertRaisesRegex(n.NativePromptError,'worker_timeout'):adapter.generate(data)
                    with self.assertRaisesRegex(n.NativePromptError,'not_ready'):adapter.generate(data)
                    with self.assertRaisesRegex(n.NativePromptError,'completion_unknown'):adapter.finish()
                child.kill.assert_called_once();self.assertEqual(popen.call_count,1)
                adapter._http.assert_not_called()
                self.assertEqual(adapter.state,'uncertain')
                result=json.loads((adapter.evidence_dir/'worker-0000.progress').read_bytes())
                self.assertEqual(result['last']['stage'],stage)
                self.assertFalse(result['server_completion_verified'])
                self.assertEqual((adapter.evidence_dir/'worker-0000.json').read_bytes(),b'')


if __name__=='__main__':unittest.main()
