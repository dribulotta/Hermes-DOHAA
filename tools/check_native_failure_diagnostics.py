#!/usr/bin/env python3
"""Seven bounded stream fixtures through installed native AIAgent; no LLM."""
import argparse
import base64
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hermes_dohaa.learning import collection, shadow
from hermes_dohaa.learning.native_prompt import (MAX_RESPONSE_WIRE, NativePromptAdapter, NativePromptError,
                                                native_adapter_sha256, parse_wire_response, validate_wire_request)
from check_native_shadow_adapter import Handler as BaseHandler, build_adapter
from check_native_development_workflow import idle_identity

SCENARIOS = ('complete', 'long-reasoning', 'read-error', 'oversized', 'invalid-json', 'incomplete-sse', 'http-status')
EXPECTED = {'read-error': ('transport_error', 'read'), 'oversized': ('wire_limit', 'read'),
            'invalid-json': ('response_invalid', 'parse'),
            'incomplete-sse': ('response_incomplete', 'parse'), 'http-status': ('http_status', 'http_status')}


class Handler(BaseHandler):
    def do_POST(self):
        if self.path == '/v1/chat/completions' and self.server.generations:
            # This records any unexpected network retry as a failed check.
            self.server.extra_generations += 1
            return BaseHandler.reply(self, {'error': 'fixture budget exceeded'}, 429)
        return super().do_POST()

    def reply(self, value, status=200, content_type='application/json'):
        if self.path != '/v1/chat/completions':
            return super().reply(value, status, content_type)
        mode = self.server.scenario
        if mode == 'long-reasoning':
            chunk = {'id': 'chatcmpl-' + 's' * 220, 'created': 1, 'model': self.server.model,
                'object': 'chat.completion.chunk', 'choices': [{'index': 0,
                    'delta': {'reasoning_content': 'r'}, 'finish_reason': None}]}
            data = (b'data: ' + shadow._canonical(chunk) + b'\n\n') * 8000 + value
            assert 512 * 1024 < len(data) < MAX_RESPONSE_WIRE
            return BaseHandler.reply(self, data, 200, 'text/event-stream')
        if mode == 'oversized':
            return BaseHandler.reply(self, b'x' * (MAX_RESPONSE_WIRE + 37), 200)
        if mode == 'invalid-json':
            return BaseHandler.reply(self, b'not a JSON document', 200)
        if mode == 'http-status':
            return BaseHandler.reply(self, {'error': 'synthetic unavailable'}, 503)
        if mode == 'incomplete-sse':
            return BaseHandler.reply(self, b'data: {}\n\n', 200, 'text/event-stream')
        if mode == 'read-error':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'data: {')
            self.wfile.flush()
            self.close_connection = True
            return
        return super().reply(value, status, content_type)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--native-commit', required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--worker-uid', type=int, required=True)
    parser.add_argument('--worker-gid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert os.name == 'posix' and os.geteuid() == 0
    assert git(ROOT, 'rev-parse', 'HEAD') == args.source_commit
    assert not git(ROOT, 'status', '--porcelain', '--untracked-files=no')
    assert git(args.native_source, 'rev-parse', 'HEAD') == args.native_commit
    assert not git(args.native_source, 'status', '--porcelain', '--untracked-files=no')
    idle_identity(args.worker_uid, args.worker_gid)
    os.umask(0o077)
    args.output.mkdir(mode=0o700, exist_ok=False)
    protocol = {'scope': 'new-native-failure-loopback-fixtures', 'source_commit': args.source_commit,
        'source_tree': git(ROOT, 'rev-parse', 'HEAD^{tree}'), 'native_commit': args.native_commit,
        'driver_sha256': shadow._hash(Path(__file__).read_bytes()), 'scenarios': list(SCENARIOS),
        'expected_failures': EXPECTED, 'max_simulated_generations': len(SCENARIOS), 'real_model_requests': 0,
        'client_concurrency': 1, 'retries': 0, 'learning_study': False}
    shadow._publish(args.output / 'protocol.json', shadow._canonical(protocol))
    report = {'schema_version': 'hermes-native-failure-conformance/1.0', 'status': 'failed',
        'protocol_sha256': shadow._hash(shadow._canonical(protocol)), 'source_commit': args.source_commit,
        'source_tree': protocol['source_tree'], 'real_model_requests': 0,
        'activation_authorized': False, 'scenarios': []}
    started = time.monotonic()
    try:
        for mode in SCENARIOS:
            idle_identity(args.worker_uid, args.worker_gid)
            folder = args.output / mode
            folder.mkdir(mode=0o700)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.model = 'synthetic-diagnostic-control'
            server.scenario, server.loaded, server.generations, server.unloads = mode, {}, [], []
            server.extra_generations = 0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                adapter, cp = build_adapter(args, server, folder)
                if mode == 'long-reasoning':
                    policy = dict(adapter.policy, max_tokens=8192, reasoning_effort='medium')
                    raw = shadow._canonical(policy)
                    cp = shadow._canonical(collection.create_collection_policy(adapter_sha256=native_adapter_sha256(),
                        runtime_policy_sha256=shadow._hash(raw), result_fields={'answer': 'integer'}))
                    adapter = NativePromptAdapter(policy_bytes=raw, expected_policy_sha256=shadow._hash(raw),
                        collection_policy_bytes=cp, native_source=args.native_source, python=args.python,
                        evidence_dir=folder/'native-traces', api_key='synthetic-no-live-credential')
                adapter.start()
                text, prompt = 'New isolated diagnostic control ' + mode, 'Return JSON with answer 7 and no actions.'
                logical = {'schema_version': 'hermes-shadow-request/1.0',
                    'request_id': shadow._hash(mode.encode()), 'input': text, 'prompt': prompt,
                    'input_sha256': shadow._hash(text.encode()), 'prompt_sha256': shadow._hash(prompt.encode()),
                    'execution_policy_sha256': shadow._hash(cp)}
                code, receipt = None, None
                try:
                    receipt = adapter.generate(shadow._canonical(logical))
                except NativePromptError as exc:
                    code = exc.code
                trace = json.loads((folder / 'native-traces/worker-0000.json').read_bytes())
                wire = base64.b64decode(trace['wire_request_base64'], validate=True)
                validate_wire_request(wire, logical, adapter.policy)
                captured = base64.b64decode(trace['wire_response_base64'], validate=True)
                profiles = [p.stat() for p in adapter.worker_root.glob('profile-*')]
                checks = {'one_generation': len(server.generations) == trace['actual_requests'] == adapter.calls == 1
                    and server.extra_generations == 0,
                    'native_identity_profile': trace['agent_class'] == 'AIAgent' and trace['identity_isolated'] is True
                    and trace['profile_passed'] is True and trace['uid'] == args.worker_uid and trace['gid'] == args.worker_gid,
                    'request_bound': trace['request_sha256'] == shadow._hash(shadow._canonical(logical))
                    and json.loads(wire) == server.generations[0],
                    'sealed_profile': len(profiles) == 1 and profiles[0].st_uid == profiles[0].st_gid == 0
                    and stat.S_IMODE(profiles[0].st_mode) == 0o700}
                if mode in ('complete', 'long-reasoning'):
                    checks['complete_receipt'] = code is None and json.loads(receipt)['status'] == 'completed'
                    checks['no_failure_diagnostic'] = trace.get('generation_failure') is None
                    if mode == 'long-reasoning':
                        checks['large_complete_trace'] = (folder/'native-traces/worker-0000.json').stat().st_size > 4 * 1024 * 1024
                        checks['all_reasoning_retained'] = parse_wire_response(captured, server.model)['reasoning_characters'] == 8000
                        checks['requested_long_reasoning_budget'] = server.generations[0]['max_tokens'] == 8192 \
                            and server.generations[0]['reasoning_effort'] == 'medium'
                    collection._lifecycle(adapter.finish())
                    checks['exact_owned_cleanup'] = adapter.state == 'closed' and not server.loaded \
                        and server.unloads == [{'instance_id': 'synthetic-instance'}]
                else:
                    failure = trace.get('generation_failure', {})
                    expected_code, expected_stage = EXPECTED[mode]
                    checks['first_failure_code_stage'] = code == failure.get('code') == 'native_shadow.' + expected_code \
                        and failure.get('stage') == expected_stage
                    checks['bounded_partial_capture'] = failure.get('response_bytes_retained') == len(captured) <= MAX_RESPONSE_WIRE \
                        and failure.get('response_bytes_observed', -1) >= len(captured) > 0
                    checks['incomplete_remains_uncertain'] = trace['server_finished'] is False \
                        and receipt is None and adapter.state == 'uncertain'
                    blocked = []
                    for call in (lambda: adapter.generate(shadow._canonical(logical)), adapter.finish):
                        try:
                            call()
                        except NativePromptError as exc:
                            blocked.append(exc.code)
                    checks['no_new_calls_or_blind_unload'] = blocked == ['native_shadow.not_ready', 'native_shadow.completion_unknown'] \
                        and not server.unloads and len(server.generations) == 1 and server.extra_generations == 0
                row = {'scenario': mode, 'checks': checks, 'status': 'passed' if all(checks.values()) else 'failed',
                       'error_code': code, 'simulated_generations': len(server.generations) + server.extra_generations}
                report['scenarios'].append(row)
                if row['status'] != 'passed':
                    raise RuntimeError('synthetic conformance failed')
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
        report['status'] = 'passed'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['check_count'] = sum(len(row['checks']) for row in report['scenarios'])
        shadow._publish(args.output / 'summary.json', shadow._canonical(report))
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
