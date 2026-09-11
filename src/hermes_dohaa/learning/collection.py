"""Collect paired prompt calls through an operator-owned adapter; never activate it.

The adapter is trusted infrastructure, not candidate code. Its lifecycle and
terminal receipts are assertions, not remote execution attestation. Keep its
installation immutable and audit its wire transport separately before live use.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import os
import uuid
from pathlib import Path
from typing import Any, Protocol

from . import shadow
from .artifacts import CandidateSnapshot

MAX_TEXT_BYTES = 64 * 1024
MAX_RECORDING_BYTES = shadow.MAX_JSON_BYTES
_ZERO = '0' * 64
_LIFECYCLE = {'schema_version': 'hermes-shadow-lifecycle/1.0',
              'idle': True, 'models_unloaded': True}


class CollectionError(shadow.ShadowError):
    """Stable diagnostics only; never expose adapter exceptions or private text."""


class PromptAdapter(Protocol):
    """Trusted, synchronous, bounded adapter with one fresh session per request.

    start verifies an idle, unloaded backend; finish verifies its own instances
    unloaded. Both return the strict lifecycle JSON receipt. generate consumes
    exactly the supplied request bytes and returns a terminal JSON receipt.
    It must enforce the pinned runtime policy, block extra requests/tools, and
    establish server completion (a client timeout does not establish that).
    """

    runtime_policy_sha256: str

    def start(self) -> bytes: ...
    def generate(self, request_bytes: bytes) -> bytes: ...
    def finish(self) -> bytes: ...


def adapter_source_sha256(adapter: PromptAdapter) -> str:
    """Hash the adapter's source module, not dependencies or runtime config."""
    try:
        source = Path(inspect.getfile(type(adapter))).read_bytes()
        return shadow._hash(source.replace(b'\r\n', b'\n'))
    except (OSError, TypeError) as exc:
        raise CollectionError('collection.adapter_unavailable') from exc


def collector_sha256() -> str:
    try:
        return shadow._hash(shadow._canonical({
            'collection.py': shadow._hash(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
            'evaluator_sha256': shadow.evaluator_sha256(),
        }))
    except OSError as exc:
        raise CollectionError('collection.source_unavailable') from exc


def create_collection_policy(*, adapter_sha256: str, runtime_policy_sha256: str,
                             result_fields: dict[str, str]) -> dict[str, Any]:
    shadow._digest(adapter_sha256)
    shadow._digest(runtime_policy_sha256)
    return {
        'schema_version': 'hermes-shadow-collection-policy/1.0',
        'collector_sha256': collector_sha256(), 'adapter_source_sha256': adapter_sha256,
        'runtime_policy_sha256': runtime_policy_sha256,
        'result_fields': shadow._result_fields(result_fields), 'concurrency': 1,
        'order': 'alternating-baseline-first/1.0', 'retries': 0,
        'max_text_bytes': MAX_TEXT_BYTES, 'max_recording_bytes': MAX_RECORDING_BYTES,
        'candidate_kind': 'prompt',
    }


def _decode(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes:
        raise CollectionError('collection.bytes_required')
    return shadow._json(data, shadow._hash(data))


def _text(value: Any) -> str:
    if type(value) is not str or not value or len(value.encode('utf-8')) > MAX_TEXT_BYTES:
        raise CollectionError('collection.text_limit')
    return value


def _prepare(snapshot, candidate_id, plan_bytes, plan_sha, suite_bytes, policy_bytes, inputs_bytes):
    try:
        return _prepare_unchecked(snapshot, candidate_id, plan_bytes, plan_sha,
                                  suite_bytes, policy_bytes, inputs_bytes)
    except (KeyError, TypeError, UnicodeError) as exc:
        raise CollectionError('collection.input_invalid') from exc


def _prepare_unchecked(snapshot, candidate_id, plan_bytes, plan_sha, suite_bytes, policy_bytes, inputs_bytes):
    candidate = shadow._snapshot(snapshot, candidate_id)
    if candidate['kind'] != 'prompt':
        raise CollectionError('collection.prompt_only')
    plan = shadow._json(plan_bytes, plan_sha)
    policy = shadow._json(policy_bytes, plan['execution_policy_sha256'])
    expected_policy = create_collection_policy(
        adapter_sha256=policy.get('adapter_source_sha256'),
        runtime_policy_sha256=policy.get('runtime_policy_sha256'),
        result_fields=policy.get('result_fields'))
    if not shadow._equal(policy, expected_policy):
        raise CollectionError('collection.policy_mismatch')
    cases = shadow._suite(suite_bytes, plan['suite_sha256'])
    raw = _decode(inputs_bytes)
    shadow._fields(raw, {'schema_version', 'inputs'})
    if raw['schema_version'] != 'hermes-shadow-inputs/1.0' or type(raw['inputs']) is not dict:
        raise CollectionError('collection.inputs_invalid')
    inputs = raw['inputs']
    if set(inputs) != {case['case_id'] for case in cases}:
        raise CollectionError('collection.input_set_mismatch')
    for case in cases:
        text = _text(inputs[case['case_id']])
        if shadow._hash(text.encode('utf-8')) != case['input_sha256']:
            raise CollectionError('collection.input_mismatch')
    try:
        prompts = {'baseline': _text(snapshot.baseline.content.decode('utf-8')),
                   'candidate': _text(candidate['artifact'])}
    except UnicodeError as exc:
        raise CollectionError('collection.prompt_encoding') from exc
    bindings = {'plan_sha256': plan_sha, **{key: plan[key] for key in (
        'candidate_id', 'baseline_sha256', 'suite_sha256', 'execution_policy_sha256')}}
    observations = {'schema_version': 'hermes-shadow-observations/1.0', **bindings,
                    'trials': [{**{key: case[key] for key in ('case_id', 'input_sha256')},
                               **{arm: {'status': 'failed', 'error_code': 'cancelled'}
                                  for arm in prompts}} for case in cases]}
    # Reuse the scorer's complete plan/criteria validation BEFORE invoking an adapter.
    _score(snapshot, candidate_id, plan_bytes, plan_sha, suite_bytes, observations)
    return policy, cases, inputs, prompts, observations


def _score(snapshot, candidate_id, plan_bytes, plan_sha, suite_bytes, observations):
    data = shadow._canonical(observations)
    return shadow.evaluate_shadow(snapshot, expected_candidate_id=candidate_id,
        plan_bytes=plan_bytes, expected_plan_sha256=plan_sha, suite_bytes=suite_bytes,
        observations_bytes=data, expected_observations_sha256=shadow._hash(data))


def _schedule(cases):
    for index in range(len(cases)):
        arms = ('baseline', 'candidate') if index % 2 == 0 else ('candidate', 'baseline')
        for arm in arms:
            yield index, arm


def _request(run_id, sequence, case, text, prompt, policy_sha):
    return shadow._canonical({
        'schema_version': 'hermes-shadow-request/1.0',
        'request_id': shadow._hash(f'{run_id}:{sequence}'.encode('ascii')),
        'input_sha256': case['input_sha256'], 'prompt_sha256': shadow._hash(prompt.encode('utf-8')),
        'execution_policy_sha256': policy_sha, 'input': text, 'prompt': prompt,
    })


def _terminal(data: bytes, request: bytes, fields: dict[str, str]):
    receipt = _decode(data)
    common = {'schema_version', 'request_sha256', 'status', 'server_finished'}
    if (receipt.get('schema_version') != 'hermes-shadow-terminal/1.0'
            or receipt.get('request_sha256') != shadow._hash(request)
            or receipt.get('server_finished') is not True):
        raise CollectionError('collection.completion_unproven')
    if receipt.get('status') == 'completed':
        shadow._fields(receipt, common | {'content'})
        if type(receipt['content']) is not str or len(receipt['content'].encode('utf-8')) > MAX_TEXT_BYTES:
            raise CollectionError('collection.response_limit')
        return shadow.admit_shadow_response(receipt['content'], result_fields=fields)['outcome']
    shadow._fields(receipt, common | {'error_code'})
    outcome = {'status': receipt['status'], 'error_code': receipt['error_code']}
    shadow._score(outcome, None)
    return outcome


def _lifecycle(data: bytes):
    if not shadow._equal(_decode(data), _LIFECYCLE):
        raise CollectionError('collection.lifecycle_unproven')


def _call(method, *args):
    try:
        return method(*args)
    except (Exception, KeyboardInterrupt):
        # Even an exception claiming to be a ShadowError is adapter-supplied.
        raise CollectionError('collection.adapter_error') from None


def _capture(data: bytes):
    # Preserve even invalid UTF-8/JSON receipts for diagnosis. Never reinterpret
    # oversized or incomplete captures as valid terminal failures.
    if type(data) is not bytes or len(data) > 8 * MAX_TEXT_BYTES:
        raise CollectionError('collection.receipt_limit')
    return {'receipt_base64': base64.b64encode(data).decode('ascii')}


def _uncapture(payload):
    shadow._fields(payload, {'receipt_base64'})
    try:
        data = base64.b64decode(payload['receipt_base64'], validate=True)
        if not shadow._equal(payload, _capture(data)):
            raise CollectionError('collection.capture_invalid')
        return data
    except (ValueError, TypeError, binascii.Error) as exc:
        raise CollectionError('collection.capture_invalid') from exc


def _manifest(run_id, plan_sha, policy_bytes, inputs_bytes):
    return {'schema_version': 'hermes-shadow-collection/1.0', 'run_id': run_id,
            'plan_sha256': plan_sha, 'policy_sha256': shadow._hash(policy_bytes),
            'inputs_sha256': shadow._hash(inputs_bytes), 'collector_sha256': collector_sha256()}


class _Journal:
    def __init__(self, directory: Path):
        if os.name != 'posix':
            raise CollectionError('collection.platform_unsupported')
        # Claim a fresh private directory; never resume or overwrite an interrupted run.
        directory.mkdir(mode=0o700, exist_ok=False)
        parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        self.directory, self.events, self.head = directory, [], _ZERO
        self.size = 0

    def append(self, kind, payload, *, emergency=False):
        event = {'sequence': len(self.events), 'previous_sha256': self.head,
                 'kind': kind, 'payload': payload}
        data = shadow._canonical(event)
        # Reserve room for safe abort/cleanup events even after an exhausted budget.
        if self.size + len(data) > MAX_RECORDING_BYTES - (0 if emergency else 16384):
            raise CollectionError('collection.recording_limit')
        shadow._publish(self.directory / f'{len(self.events):04d}.json', data)
        self.events.append(event)
        self.head, self.size = shadow._hash(data), self.size + len(data)


def collect_prompt_shadow(
    snapshot: CandidateSnapshot, *, expected_candidate_id: str, plan_bytes: bytes,
    expected_plan_sha256: str, suite_bytes: bytes, policy_bytes: bytes, inputs_bytes: bytes,
    adapter: PromptAdapter, output_dir: Path,
) -> dict[str, Any]:
    """Invoke only a trusted adapter and persist every dispatch before making it.

    The suite, evidence, arm names and feedback never enter adapter requests.
    Caller must retain plan/policy hashes independently before collection. An
    exception or invalid terminal receipt stops all further requests and cleanup;
    the backend may still be executing. No retries or interrupted-run resume.
    """
    policy, cases, inputs, prompts, observations = _prepare(
        snapshot, expected_candidate_id, plan_bytes, expected_plan_sha256,
        suite_bytes, policy_bytes, inputs_bytes)
    if (adapter_source_sha256(adapter) != policy['adapter_source_sha256']
            or adapter.runtime_policy_sha256 != policy['runtime_policy_sha256']):
        raise CollectionError('collection.adapter_mismatch')
    journal = _Journal(Path(output_dir))
    run_id, uncertain, started, finished = uuid.uuid4().hex, False, False, False
    try:
        for name, data in (('plan', plan_bytes), ('policy', policy_bytes), ('inputs', inputs_bytes)):
            shadow._publish(journal.directory / f'{name}.json', data)
        journal.append('manifest', _manifest(run_id, expected_plan_sha256, policy_bytes, inputs_bytes))
        # Assume uncertainty until the lifecycle receipt proves the initial state.
        uncertain = True
        initial = _call(adapter.start)
        journal.append('started', _capture(initial))
        _lifecycle(initial)
        uncertain, started = False, True
        for sequence, (index, arm) in enumerate(_schedule(cases)):
            case = cases[index]
            request = _request(run_id, sequence, case, inputs[case['case_id']],
                               prompts[arm], shadow._hash(policy_bytes))
            journal.append('dispatch', {'case_index': index, 'arm': arm,
                                        'request': request.decode('utf-8')})
            uncertain = True
            receipt = _call(adapter.generate, request)
            journal.append('terminal', _capture(receipt))
            outcome = _terminal(receipt, request, policy['result_fields'])
            uncertain = False
            observations['trials'][index][arm] = outcome
        uncertain = True
        cleanup = _call(adapter.finish)
        journal.append('finished', _capture(cleanup))
        _lifecycle(cleanup)
        uncertain, finished = False, True
        recording = shadow._canonical({'events': journal.events})
        report = audit_prompt_collection(snapshot, expected_candidate_id=expected_candidate_id,
            plan_bytes=plan_bytes, expected_plan_sha256=expected_plan_sha256,
            suite_bytes=suite_bytes, policy_bytes=policy_bytes, inputs_bytes=inputs_bytes,
            recording_bytes=recording, expected_recording_sha256=shadow._hash(recording))
        shadow._publish(journal.directory / 'recording.json', recording)
        shadow._publish(journal.directory / 'result.json', shadow._canonical(report))
        return report
    except (Exception, KeyboardInterrupt) as exc:
        code = exc.code if isinstance(exc, shadow.ShadowError) else 'collection.adapter_or_storage_error'
        if started and not uncertain and not finished:
            # A local storage/budget failure after a terminal receipt is known idle.
            uncertain = True
            try:
                cleanup = _call(adapter.finish)
                _lifecycle(cleanup)
                uncertain, finished = False, True
                journal.append('finished', _capture(cleanup), emergency=True)
            except (Exception, KeyboardInterrupt):
                pass
        report = {'status': 'aborted', 'error_code': code, 'run_id': run_id,
                  'server_state_uncertain': uncertain, 'cleanup_confirmed': finished,
                  'candidate_state': 'quarantined', 'activation_authorized': False}
        # Storage failure may prevent the abort marker. Earlier dispatch files
        # still identify the incomplete run. Never claim durable abortion then.
        journal.append('aborted', report, emergency=True)
        return dict(report, journal_head_sha256=journal.head)


def audit_prompt_collection(
    snapshot: CandidateSnapshot, *, expected_candidate_id: str, plan_bytes: bytes,
    expected_plan_sha256: str, suite_bytes: bytes, policy_bytes: bytes, inputs_bytes: bytes,
    recording_bytes: bytes, expected_recording_sha256: str,
) -> dict[str, Any]:
    """Reconstruct observations from exact raw receipts; no adapter calls or writes.

    This verifies recorded invocation consistency under a trusted adapter. A
    fabricated self-consistent transcript cannot prove server execution.
    """
    policy, cases, inputs, prompts, observations = _prepare(
        snapshot, expected_candidate_id, plan_bytes, expected_plan_sha256,
        suite_bytes, policy_bytes, inputs_bytes)
    raw = shadow._json(recording_bytes, expected_recording_sha256)
    shadow._fields(raw, {'events'})
    events = raw['events']
    if type(events) is not list or len(events) != 3 + 4 * len(cases):
        raise CollectionError('collection.incomplete')
    previous = _ZERO
    for sequence, event in enumerate(events):
        shadow._fields(event, {'sequence', 'previous_sha256', 'kind', 'payload'})
        if type(event['sequence']) is not int or event['sequence'] != sequence or event['previous_sha256'] != previous:
            raise CollectionError('collection.chain_invalid')
        previous = shadow._hash(shadow._canonical(event))
    manifest = events[0]['payload']
    if type(manifest) is not dict:
        raise CollectionError('collection.manifest_invalid')
    run_id = manifest.get('run_id')
    if type(run_id) is not str or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise CollectionError('collection.run_id_invalid')
    if events[0]['kind'] != 'manifest' or not shadow._equal(manifest, _manifest(
            run_id, expected_plan_sha256, policy_bytes, inputs_bytes)):
        raise CollectionError('collection.manifest_mismatch')
    for index, kind in ((1, 'started'), (-1, 'finished')):
        event = events[index]
        if event['kind'] != kind:
            raise CollectionError('collection.lifecycle_unproven')
        _lifecycle(_uncapture(event['payload']))
    for sequence, (index, arm) in enumerate(_schedule(cases)):
        dispatch, terminal = events[2 + 2 * sequence:4 + 2 * sequence]
        case = cases[index]
        request = _request(run_id, sequence, case, inputs[case['case_id']],
                           prompts[arm], shadow._hash(policy_bytes))
        expected = {'case_index': index, 'arm': arm, 'request': request.decode('utf-8')}
        if dispatch['kind'] != 'dispatch' or not shadow._equal(dispatch['payload'], expected):
            raise CollectionError('collection.dispatch_mismatch')
        if terminal['kind'] != 'terminal':
            raise CollectionError('collection.terminal_invalid')
        observations['trials'][index][arm] = _terminal(
            _uncapture(terminal['payload']), request, policy['result_fields'])
    result = _score(snapshot, expected_candidate_id, plan_bytes, expected_plan_sha256, suite_bytes, observations)
    return {'schema_version': 'hermes-shadow-collection-result/1.0', 'status': 'completed',
            'scope': 'trusted-adapter-recording-consistency-only', 'run_id': run_id,
            'recording_sha256': expected_recording_sha256, 'journal_head_sha256': previous,
            'collector_sha256': collector_sha256(), 'request_count': 2 * len(cases),
            'cleanup_confirmed': True, 'execution_attested': False,
            'activation_authorized': False, 'candidate_state': 'quarantined', 'result': result}
