#!/usr/bin/env python3
"""Exercise installed native AIAgent through loopback fixtures, zero LLM inference.

Requires root on POSIX, a trusted pinned native installation and a disposable
unprivileged test account. It never reads an active runtime profile or credentials.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hermes_dohaa.learning import collection, shadow
from hermes_dohaa.learning.artifacts import ArtifactBytes, CandidateSnapshot
from hermes_dohaa.learning.native_prompt import NativePromptAdapter, native_adapter_sha256, native_bridge_sha256
from hermes_dohaa.learning.quarantine import build_candidate


class FixtureServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.scenario, self.loaded, self.generations, self.unloads = 'valid', {}, [], []
        self.model = 'synthetic-native-control'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, content_type='application/json'):
        data = value if isinstance(value, bytes) else shadow._canonical(value)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        server = self.server
        if self.path == '/api/v1/models':
            models = [{'key': server.model, 'type': 'llm', 'max_context_length': 131072,
                       'loaded_instances': [{'id': key, 'config': {'context_length': 131072}}
                                            for key, model in server.loaded.items() if model == server.model]}]
            models.extend({'key': model, 'type': 'llm', 'loaded_instances': [{'id': key}]}
                          for key, model in server.loaded.items() if model != server.model)
            return self.reply({'models': models})
        if self.path == '/v1/models':
            return self.reply({'object': 'list', 'data': [{'id': server.model, 'object': 'model', 'context_length': 131072}]})
        self.reply({'error': 'unexpected fixture path'}, 404)

    def do_POST(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/api/show':
            return self.reply({'error': 'synthetic metadata unavailable'}, 404)
        if self.path == '/api/v1/models/unload':
            server.unloads.append(body)
            server.loaded.pop(body['instance_id'], None)
            return self.reply({'success': True})
        if self.path != '/v1/chat/completions':
            return self.reply({'error': 'unexpected fixture path'}, 404)
        server.generations.append(body)
        server.loaded['synthetic-instance'] = server.model
        systems = '\n'.join(m.get('content', '') for m in body['messages'] if m.get('role') == 'system')
        answer = 0 if 'fixture baseline' in systems else 7
        content = json.dumps({'result': {'answer': answer}, 'actions': []})
        if server.scenario == 'fenced':
            content = '```json\n' + content + '\n```'
        finish, message = 'stop', {'role': 'assistant', 'content': content}
        if server.scenario == 'length':
            finish, message = 'length', {'role': 'assistant', 'content': '', 'reasoning_content': 'synthetic reasoning tokens'}
        model = 'incorrect-response-model' if server.scenario == 'wrong-model' else server.model
        base = {'id': 'chatcmpl-native-fixture', 'created': 1, 'model': model}
        if body.get('stream'):
            chunk = {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': message, 'finish_reason': None}]}
            end = {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                   'usage': {'prompt_tokens': 20, 'completion_tokens': 32, 'total_tokens': 52}}
            data = b'data: ' + shadow._canonical(chunk) + b'\n\ndata: ' + shadow._canonical(end) + b'\n\n'
            if server.scenario != 'truncated':
                data += b'data: [DONE]\n\n'
            return self.reply(data, content_type='text/event-stream')
        self.reply({**base, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                    'usage': {'prompt_tokens': 20, 'completion_tokens': 32, 'total_tokens': 52}})


def build_adapter(args, server, folder):
    policy = shadow._canonical({'schema_version': 'hermes-native-shadow-policy/1.0',
        'native_commit': args.native_commit, 'bridge_sha256': native_bridge_sha256(),
        'model': server.model, 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
        'reasoning_effort': 'medium' if server.scenario == 'length' else 'none',
        'seed': 917101, 'temperature': 0.0, 'top_p': 1.0, 'max_tokens': 512,
        'request_timeout_seconds': 20, 'worker_timeout_seconds': 40, 'request_limit': 4,
        'worker_uid': args.worker_uid, 'worker_gid': args.worker_gid, 'exclusive_backend': True})
    cp = shadow._canonical(collection.create_collection_policy(adapter_sha256=native_adapter_sha256(),
        runtime_policy_sha256=shadow._hash(policy), result_fields={'answer': 'integer'}))
    adapter = NativePromptAdapter(policy_bytes=policy, expected_policy_sha256=shadow._hash(policy),
        collection_policy_bytes=cp, native_source=args.native_source, python=args.python,
        evidence_dir=folder / 'native-traces', api_key='synthetic-no-live-credential')
    return adapter, cp


def study_arguments(cp):
    baseline, evidence = b'fixture baseline', b'private synthetic training record'
    candidate = build_candidate({'schema_version': 'hermes-learning-draft/1.0', 'kind': 'prompt',
        'baseline_sha256': shadow._hash(baseline), 'artifact': 'fixture candidate',
        'rationale': 'Native adapter conformance, not a learning experiment.',
        'evidence_sha256': [shadow._hash(evidence)]})
    snapshot = CandidateSnapshot(candidate, ArtifactBytes(shadow._hash(baseline), baseline),
                                 (ArtifactBytes(shadow._hash(evidence), evidence),))
    inputs = {key: shadow.render_shadow_request(shadow._canonical({'public_control': key}),
        result_fields={'answer': 'integer'}) for key in ('alpha', 'beta')}
    suite = shadow._canonical({'schema_version': 'hermes-shadow-suite/1.0', 'cases': [
        {'case_id': key, 'input_sha256': shadow._hash(value.encode()), 'expected_result': {'answer': 7}}
        for key, value in inputs.items()]})
    plan = shadow._canonical(shadow.create_plan(snapshot, expected_candidate_id=candidate.candidate_id,
        suite_bytes=suite, expected_suite_sha256=shadow._hash(suite), execution_policy_sha256=shadow._hash(cp),
        min_improvements=1, min_candidate_correct=2))
    return dict(snapshot=snapshot, expected_candidate_id=candidate.candidate_id, plan_bytes=plan,
        expected_plan_sha256=shadow._hash(plan), suite_bytes=suite, policy_bytes=cp,
        inputs_bytes=shadow._canonical({'schema_version': 'hermes-shadow-inputs/1.0', 'inputs': inputs}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--native-commit', required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--worker-uid', type=int, required=True)
    parser.add_argument('--worker-gid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=['valid', 'length', 'fenced', 'wrong-model', 'truncated'], default='valid')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        parser.error('requires an isolated POSIX development host with a privileged launcher')
    os.umask(0o077)
    args.output.mkdir(mode=0o700, exist_ok=False)
    server = FixtureServer()
    server.scenario = args.scenario
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {'schema_version': 'hermes-native-shadow-conformance/1.0',
              'scope': 'installed-native-agent-loopback-fixtures-only', 'real_model_requests': 0,
              'scenario': args.scenario, 'checks': {}}
    try:
        adapter, cp = build_adapter(args, server, args.output)
        oracle = args.output / 'protected-oracle-marker'
        oracle.write_text('PRIVATE synthetic oracle marker never sent to worker', encoding='utf-8')
        probe = subprocess.run([str(args.python), '-c',
            'import pathlib,sys;\ntry: pathlib.Path(sys.argv[1]).read_bytes()\nexcept PermissionError: sys.exit(0)\nelse: sys.exit(1)',
            str(oracle)], user=args.worker_uid, group=args.worker_gid, extra_groups=[],
            cwd='/tmp', env={'PATH': os.defpath}, capture_output=True)
        report['checks']['oracle_file_unreadable_as_worker'] = probe.returncode == 0
        arguments = study_arguments(cp)
        result = collection.collect_prompt_shadow(**arguments, adapter=adapter, output_dir=args.output / 'collection')
        (args.output / 'collection-safe.json').write_bytes(shadow._canonical(result))
        traces = []
        for path in sorted((args.output / 'native-traces').glob('worker-*.json')):
            try:
                traces.append(json.loads(path.read_bytes()))
            except (ValueError, UnicodeError):
                traces.append({'trace_invalid': True})
        report['checks']['actual_native_agent'] = bool(traces) and all(t.get('agent_class') == 'AIAgent' for t in traces)
        report['checks']['restricted_profiles_and_unprivileged_workers'] = bool(traces) and all(
            t.get('profile_passed') is True and t.get('identity_isolated') is True
            and t.get('uid') == args.worker_uid and t.get('gid') == args.worker_gid for t in traces)
        profiles = list(adapter.worker_root.glob('profile-*')) if adapter.worker_root else []
        report['checks']['completed_profiles_sealed_from_later_workers'] = bool(profiles) and all(
            p.stat().st_uid == 0 and p.stat().st_mode & 0o777 == 0o700 for p in profiles)
        if args.scenario in {'valid', 'length', 'fenced'}:
            report['checks']['complete_paired_collection'] = result['status'] == 'completed' and len(server.generations) == 4
            report['checks']['single_generation_per_worker'] = len(traces) == 4 and all(t.get('actual_requests') == 1 for t in traces)
            report['checks']['owned_instance_unload_verified'] = server.unloads == [{'instance_id': 'synthetic-instance'}] and not server.loaded
            if result['status'] == 'completed':
                recording = (args.output / 'collection/recording.json').read_bytes()
                audited = collection.audit_prompt_collection(**arguments, recording_bytes=recording,
                    expected_recording_sha256=shadow._hash(recording))
                report['checks']['independent_collection_reconstruction'] = audited == result
                report['checks']['expected_scoring'] = (
                    result['result']['verdict'] == 'meets_predeclared_criteria' if args.scenario == 'valid'
                    else result['result']['summary']['candidate']['failures'] == 2)
            if args.scenario == 'length':
                report['checks']['native_fallback_blocked'] = all(t.get('denied_continuations', 0) >= 1 for t in traces)
        else:
            report['checks']['invalid_transport_stops_collection'] = result['status'] == 'aborted' and len(server.generations) == 1
            report['checks']['uncertain_work_never_unloaded'] = not server.unloads and adapter.state == 'uncertain'
        report.update(status='passed' if all(report['checks'].values()) else 'failed',
                      synthetic_generation_requests=len(server.generations), native_worker_count=len(traces))
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (args.output / 'summary.json').write_bytes(shadow._canonical(report))
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
