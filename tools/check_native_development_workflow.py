#!/usr/bin/env python3
"""Six simulated generations through native collection, selection and reversal.

Requires an isolated POSIX dev host, root launcher, reserved unprivileged worker
identity and pinned trusted native/source installs. Never reads active profiles
or credentials, and never contacts a real LLM provider. Fixtures are not learning.
"""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT/'src'))
from hermes_dohaa.learning import collection, development, review, shadow
from hermes_dohaa.learning.artifacts import load_artifact_snapshot
from hermes_dohaa.learning.native_prompt import (
    NativePromptAdapter, native_adapter_sha256, native_bridge_sha256,
    parse_wire_response, validate_wire_request,
)
from hermes_dohaa.learning.quarantine import freeze_candidate
from check_native_shadow_adapter import Handler as MetadataHandler

BASELINE = b'development workflow baseline prompt'
CANDIDATE = b'development workflow candidate prompt'
REASON = shadow._hash(b'Isolated synthetic workflow conformance; no activation authority')
PHASES = ('collection', 'selected', 'restored')
CHECKS = ('sources_pinned_and_clean', 'worker_identity_idle', 'oracle_access_denied',
    'protocol_written_before_generations', 'paired_collection_completed',
    'collection_independently_reconstructed', 'synthetic_positive_fixture_expected',
    'selection_initialized_on_baseline', 'reviewed_candidate_adopted',
    'selected_candidate_reaches_native', 'revoked_reads_denied_before_generation',
    'exact_baseline_restored', 'restored_baseline_reaches_native',
    'all_six_wire_requests_bound', 'all_six_terminal_responses_bound',
    'actual_native_agents', 'restricted_unprivileged_workers', 'all_profiles_sealed',
    'one_generation_per_worker', 'three_blocks_unloaded_exact_instances',
    'six_generation_budget', 'all_state_and_review_records_preserved', 'no_activation_authority')


class WorkflowHandler(MetadataHandler):
    def do_POST(self):
        if self.path != '/v1/chat/completions':
            return super().do_POST()
        server = self.server
        server.attempts += 1
        if server.attempts > 6:
            return self.reply({'error': 'fixture generation budget exceeded'}, 429)
        length = int(self.headers.get('Content-Length', 0))
        if not 0 < length <= 524288:
            return self.reply({'error': 'fixture request size'}, 400)
        raw = self.rfile.read(length)
        body = json.loads(raw)
        systems = '\n'.join(m['content'] for m in body['messages'] if m.get('role') == 'system')
        is_baseline, is_candidate = BASELINE.decode() in systems, CANDIDATE.decode() in systems
        if is_baseline == is_candidate:
            return self.reply({'error': 'fixture prompt identity'}, 400)
        users = [m['content'] for m in body['messages'] if m.get('role') == 'user']
        target = json.loads(users[0])['task']['target']
        answer = target - 1 if is_baseline else target
        content = json.dumps({'result': {'answer': answer}, 'actions': []})
        message = {'role': 'assistant', 'content': content}
        base = {'id': 'chatcmpl-development-workflow', 'created': 1, 'model': server.model}
        usage = {'prompt_tokens': 20, 'completion_tokens': 32, 'total_tokens': 52}
        if body.get('stream'):
            chunk = {**base, 'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': message, 'finish_reason': None}]}
            end = {**base, 'object': 'chat.completion.chunk', 'usage': usage,
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}
            response = b'data: '+shadow._canonical(chunk)+b'\n\ndata: '+shadow._canonical(end)+b'\n\ndata: [DONE]\n\n'
        else:
            response = shadow._canonical({**base, 'object': 'chat.completion', 'usage': usage,
                'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}]})
        server.loaded['workflow-'+server.phase] = server.model
        server.generations.append({'phase': server.phase,
            'request_base64': base64.b64encode(raw).decode(),
            'response_base64': base64.b64encode(response).decode()})
        return self.reply(response, content_type='text/event-stream' if body.get('stream') else 'application/json')


class WorkflowServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), WorkflowHandler)
        self.model = 'synthetic-development-workflow'
        self.phase, self.loaded, self.generations, self.unloads = 'collection', {}, [], []
        self.attempts = 0


def git(*args, root=ROOT):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                    stderr=subprocess.DEVNULL).strip()


def idle_identity(uid, gid):
    if uid <= 0 or gid <= 0:
        raise ValueError('unprivileged identity required')
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            if ((line.startswith('Uid:') and uid in map(int, line.split()[1:]))
                    or (line.startswith('Gid:') and gid in map(int, line.split()[1:]))
                    or (line.startswith('Groups:') and gid in map(int, line.split()[1:]))):
                raise ValueError('worker identity already in use')


def publish(path, value):
    shadow._publish(path, shadow._canonical(value))


def prepare_study(output, policy):
    artifacts = output/'artifacts'; artifacts.mkdir(mode=0o700)
    evidence = b'New synthetic development workflow evidence. Not learned behavior.'
    for data in (BASELINE, evidence):
        shadow._publish(artifacts/shadow._hash(data), data)
    candidate = freeze_candidate({'schema_version': 'hermes-learning-draft/1.0', 'kind': 'prompt',
        'baseline_sha256': shadow._hash(BASELINE), 'artifact': CANDIDATE.decode(),
        'rationale': 'Test the development state machine, not LLM learning.',
        'evidence_sha256': [shadow._hash(evidence)]}, output/'candidate.json')
    snapshot = load_artifact_snapshot(output/'candidate.json', expected_id=candidate.candidate_id,
                                       artifact_dir=artifacts)
    inputs = {key: shadow.render_shadow_request(shadow._canonical({'sample': key, 'target': value}),
        result_fields={'answer': 'integer'}) for key, value in [('ember', 19), ('harbor', 23)]}
    suite = shadow._canonical({'schema_version': 'hermes-shadow-suite/1.0', 'cases': [
        {'case_id': key, 'input_sha256': shadow._hash(value.encode()),
         'expected_result': {'answer': json.loads(value)['task']['target']}} for key, value in inputs.items()]})
    plan = shadow._canonical(shadow.create_plan(snapshot, expected_candidate_id=candidate.candidate_id,
        suite_bytes=suite, expected_suite_sha256=shadow._hash(suite), execution_policy_sha256=shadow._hash(policy),
        min_improvements=2, min_candidate_correct=2))
    arguments = dict(snapshot=snapshot, expected_candidate_id=candidate.candidate_id,
        plan_bytes=plan, expected_plan_sha256=shadow._hash(plan), suite_bytes=suite, policy_bytes=policy,
        inputs_bytes=shadow._canonical({'schema_version': 'hermes-shadow-inputs/1.0', 'inputs': inputs}))
    for name in ('plan_bytes', 'suite_bytes', 'inputs_bytes'):
        shadow._publish(output/(name+'.json'), arguments[name])
    return arguments


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
    if os.name != 'posix' or os.geteuid() != 0:
        parser.error('requires a privileged isolated POSIX development launcher')
    os.umask(0o077)
    args.output.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    report = {'schema_version': 'hermes-native-development-workflow/1.0',
        'scope': 'installed-native-simulated-development-workflow', 'status': 'failed',
        'real_model_requests': 0, 'learning_study': False, 'execution_attested': False,
        'activation_authorized': False, 'checks': {key: False for key in CHECKS}}
    server, thread, adapters, logical = None, None, {}, []

    def check(name, value):
        report['checks'][name] = bool(value)
        if not value:
            raise ValueError('workflow conformance check failed')

    try:
        check('sources_pinned_and_clean', git('rev-parse', 'HEAD') == args.source_commit
            and not git('status', '--porcelain', '--untracked-files=no')
            and git('rev-parse', 'HEAD', root=args.native_source) == args.native_commit
            and not git('status', '--porcelain', '--untracked-files=no', root=args.native_source))
        idle_identity(args.worker_uid, args.worker_gid)
        check('worker_identity_idle', True)
        marker = args.output/'private-oracle-marker'
        shadow._publish(marker, b'PRIVATE synthetic marker, never supplied to native workers')
        probe = subprocess.run([str(args.python), '-P', '-c',
            'import pathlib,sys;\ntry:pathlib.Path(sys.argv[1]).read_bytes()\nexcept PermissionError:sys.exit(0)\nelse:sys.exit(1)', str(marker)],
            user=args.worker_uid, group=args.worker_gid, extra_groups=[], cwd='/tmp',
            env={'PATH': os.defpath, 'PYTHONNOUSERSITE': '1'}, capture_output=True, timeout=10)
        check('oracle_access_denied', probe.returncode == 0)
        server = WorkflowServer()
        policies = {}
        for phase in PHASES:
            folder = args.output/phase; folder.mkdir(mode=0o700)
            policy = shadow._canonical({'schema_version': 'hermes-native-shadow-policy/1.0',
                'native_commit': args.native_commit, 'bridge_sha256': native_bridge_sha256(),
                'model': server.model, 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
                'reasoning_effort': 'none', 'seed': 9171803, 'temperature': 0.0, 'top_p': 1.0,
                'max_tokens': 512, 'request_timeout_seconds': 20, 'worker_timeout_seconds': 40,
                'request_limit': 4 if phase == 'collection' else 2,
                'worker_uid': args.worker_uid, 'worker_gid': args.worker_gid, 'exclusive_backend': True})
            cp = shadow._canonical(collection.create_collection_policy(adapter_sha256=native_adapter_sha256(),
                runtime_policy_sha256=shadow._hash(policy), result_fields={'answer': 'integer'}))
            shadow._publish(folder/'runtime-policy.json', policy)
            shadow._publish(folder/'collection-policy.json', cp)
            policies[phase] = (policy, cp)
            adapters[phase] = NativePromptAdapter(policy_bytes=policy, expected_policy_sha256=shadow._hash(policy),
                collection_policy_bytes=cp, native_source=args.native_source, python=args.python,
                evidence_dir=folder/'native-traces', api_key='synthetic-no-live-credential')
        arguments = prepare_study(args.output, policies['collection'][1])
        initial = development.initialize_development_selection(arguments['snapshot'],
            expected_candidate_id=arguments['expected_candidate_id'], output_dir=args.output/'selection', reason_sha256=REASON)
        check('selection_initialized_on_baseline', initial.prompt == BASELINE)
        initial_head = initial.report()['head_sha256']
        source_files = [Path(__file__).resolve(), ROOT/'tools/check_native_shadow_adapter.py',
            *[ROOT/f'src/hermes_dohaa/learning/{name}.py' for name in
              ('artifacts','quarantine','shadow','collection','review','development','native_prompt','native_worker')]]
        protocol = {'schema_version': 'hermes-native-development-workflow-protocol/1.0',
            'max_simulated_generations': 6, 'real_model_requests': 0, 'retries': 0,
            'phase_calls': {'collection': 4, 'selected': 1, 'restored': 1},
            'client_concurrency': 1, 'max_wall_seconds': 360, 'required_checks': list(CHECKS),
            'source_commit': args.source_commit, 'source_tree': git('rev-parse', 'HEAD^{tree}'),
            'native_commit': args.native_commit, 'initial_selection_head_sha256': initial_head,
            'source_hashes': {str(p.relative_to(ROOT)): shadow._hash(p.read_bytes()) for p in source_files},
            'plan_sha256': arguments['expected_plan_sha256'],
            'policy_hashes': {p: shadow._hash(policies[p][0]) for p in PHASES},
            'learning_study': False, 'execution_attested': False, 'activation_authorized': False}
        publish(args.output/'protocol.json', protocol)
        report['protocol_sha256'] = shadow._hash(shadow._canonical(protocol))
        report['source_commit'], report['source_tree'] = args.source_commit, protocol['source_tree']
        check('protocol_written_before_generations', server.attempts == 0)
        print(json.dumps({'status': 'protocol_fixed', 'protocol_sha256': report['protocol_sha256'],
                          'max_simulated_generations': 6, 'real_model_requests': 0}), flush=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        result = collection.collect_prompt_shadow(**arguments, adapter=adapters['collection'],
                                                  output_dir=args.output/'recording')
        publish(args.output/'collection-safe.json', result)
        check('paired_collection_completed', result['status'] == 'completed' and len(server.generations) == 4)
        recording = (args.output/'recording/recording.json').read_bytes()
        audit_arguments = dict(arguments, recording_bytes=recording, expected_recording_sha256=shadow._hash(recording))
        audited = collection.audit_prompt_collection(**audit_arguments)
        check('collection_independently_reconstructed', audited == result)
        check('synthetic_positive_fixture_expected', result['result']['verdict'] == 'meets_predeclared_criteria'
            and result['result']['summary']['baseline']['correct'] == 0
            and result['result']['summary']['candidate']['correct'] == 2)
        logical.extend(('collection', e['payload']['request'].encode())
                       for e in json.loads(recording)['events'] if e['kind'] == 'dispatch')
        reviews = args.output/'reviews'; reviews.mkdir(mode=0o700)
        collection_hash = shadow._hash(shadow._canonical(audited))
        recommended = review.record_collection_review(reviews, audit_arguments=audit_arguments,
            expected_collection_sha256=collection_hash, expected_previous_sha256='0'*64,
            decision='recommend_for_development', reviewer_sha256=shadow._hash(b'synthetic operator'), reason_sha256=REASON)
        approval = dict(audit_arguments=audit_arguments, expected_collection_sha256=collection_hash,
            review_directory=reviews, expected_review_head_sha256=recommended['head_sha256'])
        adopted = development.adopt_development_prompt(args.output/'selection', expected_head_sha256=initial_head,
                                                       reason_sha256=REASON, **approval)
        publish(args.output/'adopted-selection.json', adopted.report())
        check('reviewed_candidate_adopted', adopted.prompt == CANDIDATE)

        def consume(phase, head, **review_arguments):
            # Review/selection gate precedes adapter lookup, start and generation.
            selected = development.read_development_selection(args.output/'selection',
                expected_head_sha256=head, **review_arguments)
            if time.monotonic()-started >= protocol['max_wall_seconds']:
                raise ValueError('workflow wall budget')
            idle_identity(args.worker_uid, args.worker_gid)
            server.phase = phase
            adapter = adapters[phase]
            receipt = adapter.start()
            shadow._publish(args.output/phase/'start.receipt.json', receipt)
            try:
                public = shadow.render_shadow_request(shadow._canonical({'sample': 'consumer-'+phase, 'target': 31}),
                                                       result_fields={'answer': 'integer'})
                request = shadow._canonical({'schema_version': 'hermes-shadow-request/1.0',
                    'request_id': shadow._hash((report['protocol_sha256']+':'+phase).encode()),
                    'input': public, 'input_sha256': shadow._hash(public.encode()),
                    'prompt': selected.prompt.decode(), 'prompt_sha256': shadow._hash(selected.prompt),
                    'execution_policy_sha256': shadow._hash(policies[phase][1])})
                shadow._publish(args.output/phase/'request.json', request)
                logical.append((phase, request))
                response = adapter.generate(request)
                shadow._publish(args.output/phase/'terminal.receipt.json', response)
                if json.loads(response)['status'] != 'completed':
                    raise ValueError('fixture native consumption failed')
                return selected.prompt
            finally:
                if adapter.state == 'idle':
                    shadow._publish(args.output/phase/'finish.receipt.json', adapter.finish())

        check('selected_candidate_reaches_native', consume('selected', adopted.report()['head_sha256'], **approval) == CANDIDATE)
        revoked = review.record_collection_review(reviews, audit_arguments=audit_arguments,
            expected_collection_sha256=collection_hash, expected_previous_sha256=recommended['head_sha256'],
            decision='revoke', reviewer_sha256=shadow._hash(b'synthetic operator'), reason_sha256=REASON)
        denied = 0; before = server.attempts
        for pin in (recommended['head_sha256'], revoked['head_sha256']):
            try:
                consume('forbidden', adopted.report()['head_sha256'],
                        **dict(approval, expected_review_head_sha256=pin))
            except (development.DevelopmentError, collection.CollectionError) as exc:
                if exc.code not in ('review.head_mismatch', 'development.not_recommended'):
                    raise
                denied += 1
        check('revoked_reads_denied_before_generation', denied == 2 and server.attempts == before)
        restored = development.revert_development_prompt(args.output/'selection',
            expected_head_sha256=adopted.report()['head_sha256'], reason_sha256=REASON)
        publish(args.output/'restored-selection.json', restored.report())
        check('exact_baseline_restored', restored.prompt == BASELINE)
        check('restored_baseline_reaches_native', consume('restored', restored.report()['head_sha256']) == BASELINE)

        traces, profiles = [], []
        requests_bound, responses_bound = True, True
        expected_prompts = [BASELINE, CANDIDATE, CANDIDATE, BASELINE, CANDIDATE, BASELINE]
        for index, (phase, request_bytes) in enumerate(logical):
            offset = index if phase == 'collection' else 0
            trace = json.loads((args.output/phase/f'native-traces/worker-{offset:04d}.json').read_bytes())
            traces.append(trace)
            request, policy = json.loads(request_bytes), json.loads(policies[phase][0])
            wire = base64.b64decode(trace['wire_request_base64'], validate=True)
            validate_wire_request(wire, request, policy)
            requests_bound &= (trace['request_sha256'] == shadow._hash(request_bytes)
                and trace['runtime_policy_sha256'] == shadow._hash(policies[phase][0])
                and trace['bridge_sha256'] == native_bridge_sha256()
                and request['prompt'].encode() == expected_prompts[index]
                and trace['wire_request_base64'] == server.generations[index]['request_base64'])
            parsed = parse_wire_response(base64.b64decode(trace['wire_response_base64'], validate=True), server.model)
            responses_bound &= (trace['wire_response_base64'] == server.generations[index]['response_base64']
                and trace['server_finished'] is True and trace['native_completed'] is True
                and trace['content_matches_wire'] is True and parsed['finish_reason'] == 'stop')
        for adapter in adapters.values():
            profiles.extend(p.lstat() for p in adapter.worker_root.glob('profile-*'))
        check('all_six_wire_requests_bound', len(logical) == 6 and requests_bound)
        check('all_six_terminal_responses_bound', len(traces) == 6 and responses_bound)
        check('actual_native_agents', all(t['agent_class'] == 'AIAgent' for t in traces))
        check('restricted_unprivileged_workers', all(t['identity_isolated'] is True and t['profile_passed'] is True
            and t['uid'] == args.worker_uid and t['gid'] == args.worker_gid for t in traces))
        check('all_profiles_sealed', len(profiles) == 6 and all(stat.S_ISDIR(p.st_mode) and p.st_uid == p.st_gid == 0
            and stat.S_IMODE(p.st_mode) == 0o700 for p in profiles))
        check('one_generation_per_worker', all(t['actual_requests'] == 1 and not t['denied_continuations'] for t in traces))
        check('three_blocks_unloaded_exact_instances', server.unloads == [{'instance_id': 'workflow-'+p} for p in PHASES]
            and not server.loaded and all(a.state == 'closed' for a in adapters.values()))
        check('six_generation_budget', server.attempts == len(server.generations) == 6
            and time.monotonic()-started <= protocol['max_wall_seconds'])
        check('all_state_and_review_records_preserved', len(list((args.output/'selection').glob('state-*.json'))) == 3
            and len(list(reviews.glob('review-*.json'))) == 2 and (args.output/'recording/recording.json').read_bytes() == recording)
        check('no_activation_authority', all(v.report()['activation_authorized'] is False
            and v.report()['execution_attested'] is False and v.report()['candidate_state'] == 'quarantined'
            for v in (initial, adopted, restored)))
        report['status'] = 'passed'
    except Exception as exc:
        report.update(error_type=type(exc).__name__)
        code = getattr(exc, 'code', None)
        if isinstance(code, str) and code.startswith(('shadow.', 'collection.', 'review.', 'development.', 'native_shadow.')):
            report['error_code'] = code
    finally:
        if server is not None:
            if thread is not None:
                server.shutdown(); thread.join(timeout=5)
            server.server_close()
            publish(args.output/'fixture-wire.private.json', server.generations)
        report.update(check_count=len(CHECKS), synthetic_generation_requests=len(server.generations) if server else 0,
            generation_attempts=server.attempts if server else 0,
            native_worker_count=sum(a.calls for a in adapters.values()), elapsed_seconds=round(time.monotonic()-started, 3))
        publish(args.output/'summary.json', report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
