"""Synthetic transport only: these tests do not measure a real LLM."""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_dohaa.learning import native_prompt as n, native_worker as w, shadow, collection
from hermes_dohaa.learning.native_response_format import response_format_for_policy, document_response_format
from test_native_shadow_adapter import policy, request, wire, response, encode, digest
from test_native_lmstudio_context import agent
from test_host_steps import step, proposal
from tools.controlled_tool_proposal import tool_response_format
from tools.controlled_tools import Conflict, IntentJournal, ToolStore
from tools.host_steps import HostStepStore
from tools.native_model_residency import ModelResidency, ResidencyError


def tool_policy():
    return dict(policy(), schema_version='hermes-native-tool-policy/1.0',
                response_contract='controlled-tool-proposal/1.0', context_length=8192)


class ToolContractTests(unittest.TestCase):
    def test_separate_fixed_contract_and_explicit_context(self):
        p = tool_policy()
        self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
        self.assertEqual(response_format_for_policy(p), tool_response_format())
        self.assertEqual(w.native_model_configuration(p)['context_length'], 8192)
        self.assertTrue(w.model_profile_checks(agent(p), p))
        a = agent(p); a.tools = ['reserve']
        self.assertFalse(w.model_profile_checks(a, p))

    def test_document_policies_never_accept_tool_contract(self):
        for version in ('1.0', '1.1', '1.2'):
            p = tool_policy(); p['schema_version'] = 'hermes-native-shadow-policy/' + version
            with self.assertRaises((ValueError, n.NativePromptError)):
                response_format_for_policy(p)
        for version in ('1.1', '1.2'):
            p = dict(tool_policy(), schema_version='hermes-native-shadow-policy/' + version,
                     response_contract='document-stream-proposal/1.0')
            self.assertEqual(response_format_for_policy(p), document_response_format())
            self.assertEqual(response_format_for_policy(p)['json_schema']['schema']['properties']['requested_actions']['maxItems'], 0)

    def test_unknown_contract_schema_and_wire_mutation_rejected(self):
        p = tool_policy(); logical = request(); payload = wire(logical, p)
        payload['response_format'] = tool_response_format()
        n.validate_wire_request(encode(payload), logical, p)
        for change in ({'response_contract': 'document-stream-proposal/1.0'},
                       {'response_format': tool_response_format()}, {'context_length': True}):
            altered = dict(p, **change)
            with self.assertRaises((ValueError, n.NativePromptError)): n.validate_native_policy(encode(altered), digest(encode(altered)))
        for fmt in (None, {'type': 'json_object'}, document_response_format()):
            altered = dict(payload)
            if fmt is None: altered.pop('response_format')
            else: altered['response_format'] = fmt
            with self.assertRaises((ValueError, n.NativePromptError)): n.validate_wire_request(encode(altered), logical, p)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged Linux evidence boundary')
class ToolEvidenceTests(unittest.TestCase):
    def setUp(self):
        from tools.native_tool_evidence import NativeToolEvidenceBridge
        self.Bridge = NativeToolEvidenceBridge
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.p = tool_policy()
        self.pb = encode(self.p); self.ps = digest(self.pb)
        self.cp = encode(collection.create_collection_policy(adapter_sha256=n.native_adapter_sha256(),
                         runtime_policy_sha256=self.ps, result_fields={'result': 'object'}))
        self.instances = []
        def catalog():
            return {'models': [{'key': self.p['model'], 'type': 'llm', 'max_context_length': 16384,
                                'loaded_instances': copy.deepcopy(self.instances)}]}
        def load(body):
            self.instances[:] = [{'id': 'owned', 'config': {'context_length': 8192}}]
            return {'status': 'loaded', 'instance_id': 'owned', 'load_config': {'context_length': 8192}}
        self.lease = ModelResidency(self.root/'residency.db', model=self.p['model'], context_length=8192,
            policy_sha256=self.ps, catalog=catalog, load=load, unload=lambda body: self.instances.clear())
        self.lease.start()
        self.tool = ToolStore(self.root/'tool.db'); self.intent = IntentJournal(self.root/'intent.db')
        self.tool.seed('widget', 10); self.tool.authorize('task-a', 'widget', 7)
        self.adapter = n.NativePromptAdapter(policy_bytes=self.pb, expected_policy_sha256=self.ps,
            collection_policy_bytes=self.cp, native_source=self.root, python=Path(sys.executable),
            evidence_dir=self.root/'native', api_key='synthetic')
        # Startup/source is a fixture here, not a claim of native setup verification.
        self.adapter.evidence_dir.mkdir(mode=0o700)
        shadow._publish(self.adapter.evidence_dir/'session.json', encode({
            'schema_version': 'hermes-native-tool-session/1.0', 'native_commit': self.p['native_commit'],
            'bridge_sha256': self.p['bridge_sha256'], 'runtime_policy_sha256': self.ps,
            'collection_policy_sha256': digest(self.cp)}))
        self.adapter.worker_root = self.root/'workers'; self.adapter.worker_root.mkdir(mode=0o755)
        self.adapter.state = 'idle'; self.adapter.owned = {'owned'}
        self.adapter._catalog = Mock(return_value={'owned': self.p['model']})
        self.bridge = self.reopen()
        self.host = HostStepStore(self.root/'host.db', journal=self.intent, tool=self.tool,
                                  terminal_verifier=self.bridge.verify)
        self.host.allocate(step())
        self.logical = request(digest(self.cp)); self.raw = encode(self.logical)
        self.trace_change = lambda trace: None
        self.sends = 0

    def reopen(self):
        return self.Bridge(self.root/'bridge', policy_bytes=self.pb, collection_policy_bytes=self.cp,
            residency=self.lease, native_evidence_dir=self.root/'native',
            host_path=self.root/'host.db', intent_path=self.root/'intent.db', effect_path=self.root/'tool.db')

    def spawn(self, argv, **kwargs):
        child = Mock(returncode=0)
        def communicate(data, timeout):
            self.sends += 1
            configuration = json.loads(data); logical = configuration['request']
            payload = wire(logical, self.p); payload['response_format'] = tool_response_format()
            trace = dict(schema_version='hermes-shadow-trace/1.0', request_sha256=digest(self.raw),
                runtime_policy_sha256=self.ps, bridge_sha256=self.p['bridge_sha256'],
                uid=self.p['worker_uid'], gid=self.p['worker_gid'], identity_isolated=True,
                agent_class='AIAgent', profile_passed=True, server_finished=True,
                actual_requests=1, native_completed=True, content_matches_wire=True, denied_continuations=0,
                wire_request_base64=base64.b64encode(encode(payload)).decode(),
                wire_response_base64=base64.b64encode(encode(response(proposal()))).decode())
            self.trace_change(trace)
            kwargs['stdout'].write(encode(trace))
        child.communicate.side_effect = communicate
        return child

    def send(self):
        with patch.object(n.subprocess, 'Popen', side_effect=self.spawn):
            return self.bridge.generate(self.host, 'op-1', self.raw, self.adapter)

    def test_verified_native_artifact_and_ownership_recover_host_without_resend(self):
        self.send()
        self.assertEqual(self.host.snapshot('op-1')['state'], 'requesting')
        self.host.terminal_verifier = self.reopen().verify
        self.assertEqual(self.host.record_terminal('op-1')['state'], 'proposed')
        with self.assertRaises(Conflict): self.send()
        self.assertEqual(self.sends, 1)
        self.assertIsNone(self.intent.pending('task-a'))
        from tools.controlled_tools import Executor
        Executor(self.intent, self.tool).execute(self.host.bound_proposal('op-1').operation)
        self.assertEqual(self.host.reconcile_effect('op-1')['state'], 'applied')
        self.assertEqual(self.tool.inventory('widget')['available'], 6)

    def test_missing_completion_never_authorizes_retry_or_unload(self):
        self.trace_change = lambda trace: trace.update(server_finished=False)
        with self.assertRaises((ValueError, n.NativePromptError)): self.send()
        self.assertEqual(self.lease.snapshot()['state'], 'generating')
        with self.assertRaises((ValueError, n.NativePromptError, Conflict, ResidencyError, OSError)): self.host.record_terminal('op-1')
        with self.assertRaises(ResidencyError): self.lease.finish()
        with self.assertRaises(Conflict): self.send()
        self.assertEqual(self.sends, 1)

    def rejected_trace(self, changes):
        self.trace_change = lambda trace: trace.update(changes)
        with self.assertRaises((ValueError, n.NativePromptError)): self.send()
        self.assertEqual(self.host.snapshot('op-1')['state'], 'requesting')
        self.assertEqual(self.lease.snapshot()['state'], 'generating')

    def test_wrong_worker_profile(self): self.rejected_trace({'profile_passed': False})
    def test_multiple_wire_requests(self): self.rejected_trace({'actual_requests': 2})
    def test_native_content_mismatch(self): self.rejected_trace({'content_matches_wire': False})
    def test_wrong_worker_identity(self): self.rejected_trace({'uid': 0})
    def test_unverified_source(self): self.rejected_trace({'bridge_sha256': 'f'*64})

    def test_document_wire_cannot_authorize_tool_proposal(self):
        payload = wire(self.logical, self.p); payload['response_format'] = document_response_format()
        self.rejected_trace({'wire_request_base64': base64.b64encode(encode(payload)).decode()})

    def test_mutated_logical_request_is_not_authorized(self):
        self.send()
        with sqlite3.connect(self.bridge.path) as db:
            db.execute('UPDATE requests SET request=?', (encode(dict(self.logical, input='different')),))
        with self.assertRaises(Conflict): self.host.record_terminal('op-1')

    def test_document_only_receipt_without_native_proof_is_rejected(self):
        self.send()
        (self.adapter.evidence_dir/(digest(self.raw)+'.terminal')).unlink()
        with self.assertRaises(OSError): self.host.record_terminal('op-1')

    def test_duplicate_keys_in_terminal_artifact_rejected(self):
        self.send(); path = self.adapter.evidence_dir/(digest(self.raw)+'.terminal')
        path.write_bytes(b'{"schema_version":"x",'+path.read_bytes()[1:])
        with self.assertRaises(ValueError): self.host.record_terminal('op-1')

    def test_receipt_content_mutation_rejected(self):
        self.send(); path = self.adapter.evidence_dir/(digest(self.raw)+'.terminal')
        raw = json.loads(path.read_bytes()); raw['receipt_base64'] = base64.b64encode(b'{}').decode()
        path.write_bytes(encode(raw))
        with self.assertRaises(n.NativePromptError): self.host.record_terminal('op-1')

    def test_source_session_mutation_rejected(self):
        self.send(); path = self.adapter.evidence_dir/'session.json'
        raw = json.loads(path.read_bytes()); raw['native_commit'] = 'b'*40
        path.write_bytes(encode(raw))
        with self.assertRaises(n.NativePromptError): self.host.record_terminal('op-1')

    def test_altered_protected_wire_is_rejected_on_restart(self):
        self.send()
        path = self.adapter.evidence_dir/'worker-0000.json'
        trace = json.loads(path.read_bytes()); trace['wire_response_base64'] = base64.b64encode(encode(response('changed'))).decode()
        path.write_bytes(encode(trace))
        with self.assertRaises((ValueError, n.NativePromptError, Conflict, ResidencyError, OSError)): self.reopen().verify(self.host.request_binding('op-1'))

    def test_missing_residency_history_is_not_replaced_by_receipt_shape(self):
        self.send()
        with sqlite3.connect(self.lease.path) as db: db.execute('DELETE FROM terminals')
        with self.assertRaises((ValueError, n.NativePromptError, Conflict, ResidencyError, OSError)): self.host.record_terminal('op-1')

    def test_host_step_and_policy_substitution_rejected(self):
        from dataclasses import replace
        self.send(); binding = self.host.request_binding('op-1')
        for change in ({'step_sha256': 'f'*64}, {'policy_sha256': 'f'*64}, {'request_sha256': 'f'*64}):
            with self.assertRaises((ValueError, n.NativePromptError, Conflict, ResidencyError, OSError)): self.bridge.verify(replace(binding, **change))

    def test_recovery_after_native_artifact_before_ownership_commit(self):
        with patch.object(self.lease, 'record_terminal', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): self.send()
        self.assertEqual(self.lease.snapshot()['state'], 'generating')
        bridge = self.reopen(); bridge.recover(self.host.request_binding('op-1'))
        self.assertEqual(self.host.record_terminal('op-1')['state'], 'proposed')
        self.assertEqual(self.sends, 1)

    def crash_and_recover(self, before_ownership):
        pid = os.fork()
        if pid == 0:
            try:
                if before_ownership: self.lease.record_terminal = lambda *a: os._exit(23)
                self.send()
                os._exit(23)
            except BaseException: os._exit(24)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 23)
        self.assertEqual(self.host.snapshot('op-1')['state'], 'requesting')
        self.reopen().recover(self.host.request_binding('op-1'))
        self.assertEqual(self.host.record_terminal('op-1')['state'], 'proposed')
        self.assertEqual(len(list(self.adapter.evidence_dir.glob('worker-*.json'))), 1)

    def test_process_crash_before_ownership_commit(self): self.crash_and_recover(True)
    def test_process_crash_before_host_admission(self): self.crash_and_recover(False)

    def test_history_survives_owned_unload(self):
        self.send(); self.lease.finish()
        self.assertEqual(self.host.record_terminal('op-1')['state'], 'proposed')
        self.assertFalse(self.instances)

    def test_worker_cannot_read_host_scope_or_artifacts(self):
        self.send()
        import subprocess
        targets = [self.host.path, self.intent.path, self.tool.path, self.lease.path,
                   self.root/'bridge'/'requests.db', self.adapter.evidence_dir/'worker-0000.json']
        script = 'import os,sys\nfor p in sys.argv[1:]:\n for mode in ("rb","ab"):\n  try: open(p,mode).close()\n  except PermissionError: continue\n  raise SystemExit(9)\n'
        result = subprocess.run([sys.executable, '-c', script, *map(str, targets)],
            user=self.p['worker_uid'], group=self.p['worker_gid'], extra_groups=[], cwd='/tmp')
        self.assertEqual(result.returncode, 0)

    def test_public_evidence_root_rejected(self):
        os.chmod(self.root/'bridge', 0o755)
        with self.assertRaises((ValueError, n.NativePromptError)): self.reopen()

    def test_symlink_evidence_rejected(self):
        self.send(); path = self.adapter.evidence_dir/(digest(self.raw)+'.terminal')
        moved = self.adapter.evidence_dir/'original'; path.rename(moved); path.symlink_to(moved)
        with self.assertRaises(n.NativePromptError): self.host.record_terminal('op-1')
