"""Preregister and score recorded paired shadow results without executing candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

from . import artifacts, quarantine
from .artifacts import ArtifactBytes, ArtifactError, CandidateSnapshot, load_artifact_snapshot
from .quarantine import Candidate, CandidateError, build_candidate

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_CASES = 256
MAX_DEPTH = 32
MAX_NODES = 100_000
_DIGEST = re.compile(r'[0-9a-f]{64}')
_CASE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}')
_EVALUATOR = 'exact-typed-json/no-actions/1.0'
_ERRORS = {'timeout', 'budget_exhausted', 'invalid_response', 'runtime_error', 'cancelled'}


class ShadowError(ValueError):
    """Stable error code; no private data or raw exception messages."""

    def __init__(self, code: str = 'shadow.invalid') -> None:
        self.code = code
        super().__init__(code)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _fields(value: Any, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        raise ShadowError('shadow.fields_invalid')


def _digest(value: Any) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ShadowError('shadow.digest_invalid')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ShadowError('shadow.duplicate_key')
        result[key] = value
    return result


def _nonfinite(_):
    raise ShadowError('shadow.nonfinite')


def _json(data: bytes, expected: str) -> dict[str, Any]:
    _digest(expected)
    if type(data) is not bytes or len(data) > MAX_JSON_BYTES:
        raise ShadowError('shadow.document_limit')
    if _hash(data) != expected:
        raise ShadowError('shadow.digest_mismatch')
    try:
        result = json.loads(data.decode('utf-8'), object_pairs_hook=_unique,
                            parse_constant=_nonfinite)
        pending = [(result, 0)]
        nodes = 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if depth > MAX_DEPTH or nodes > MAX_NODES:
                raise ShadowError('shadow.structure_limit')
            if isinstance(value, float) and not math.isfinite(value):
                raise ShadowError('shadow.nonfinite')
            if isinstance(value, str):
                value.encode('utf-8')
            elif isinstance(value, dict):
                for key, item in value.items():
                    key.encode('utf-8')
                    pending.append((item, depth + 1))
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ShadowError):
            raise
        raise ShadowError('shadow.json_invalid') from exc
    if type(result) is not dict:
        raise ShadowError('shadow.fields_invalid')
    return result


def _snapshot(snapshot: CandidateSnapshot, expected_id: str) -> dict[str, Any]:
    """Recheck bytes and identity; public data-class instances are not trust tokens."""
    _digest(expected_id)
    if type(snapshot) is not CandidateSnapshot or type(snapshot.candidate) is not Candidate:
        raise ShadowError('shadow.snapshot_invalid')
    try:
        envelope = snapshot.candidate.to_dict()
        payload = envelope['candidate']
        if type(payload) is not dict or payload.get('state') != 'quarantined':
            raise ShadowError('shadow.snapshot_invalid')
        if payload.get('schema_version') != 'hermes-learning-candidate/1.0':
            raise ShadowError('shadow.snapshot_invalid')
        draft = {key: value for key, value in payload.items() if key != 'state'}
        draft['schema_version'] = 'hermes-learning-draft/1.0'
        candidate = build_candidate(draft)
        if candidate.candidate_id != expected_id or envelope['candidate_id'] != expected_id:
            raise ShadowError('shadow.candidate_mismatch')
        if type(snapshot.evidence) is not tuple or len(snapshot.evidence) != len(payload['evidence_sha256']):
            raise ShadowError('shadow.snapshot_invalid')
        pairs = [(snapshot.baseline, payload['baseline_sha256'])]
        pairs.extend(zip(snapshot.evidence, payload['evidence_sha256']))
        unique = {}
        for item, expected in pairs:
            if (type(item) is not ArtifactBytes or type(item.content) is not bytes
                    or item.sha256 != expected):
                raise ShadowError('shadow.snapshot_invalid')
            if len(item.content) > artifacts.MAX_ARTIFACT_BYTES:
                raise ShadowError('shadow.snapshot_limit')
            if _hash(item.content) != expected:
                raise ShadowError('shadow.snapshot_mismatch')
            unique[expected] = len(item.content)
        if sum(unique.values()) > artifacts.MAX_TOTAL_BYTES:
            raise ShadowError('shadow.snapshot_limit')
        return payload
    except (CandidateError, KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ShadowError):
            raise
        raise ShadowError('shadow.snapshot_invalid') from exc


def _suite(data: bytes, expected: str) -> list[dict[str, Any]]:
    raw = _json(data, expected)
    _fields(raw, {'schema_version', 'cases'})
    if raw['schema_version'] != 'hermes-shadow-suite/1.0':
        raise ShadowError('shadow.version_invalid')
    cases = raw['cases']
    if type(cases) is not list or not 2 <= len(cases) <= MAX_CASES:
        raise ShadowError('shadow.case_count_invalid')
    identities, inputs = set(), set()
    for case in cases:
        _fields(case, {'case_id', 'input_sha256', 'expected_result'})
        identifier = case['case_id']
        if not isinstance(identifier, str) or _CASE_ID.fullmatch(identifier) is None:
            raise ShadowError('shadow.case_id_invalid')
        _digest(case['input_sha256'])
        if identifier in identities or case['input_sha256'] in inputs:
            raise ShadowError('shadow.duplicate_case')
        identities.add(identifier)
        inputs.add(case['input_sha256'])
    return cases


def _result_fields(fields: Any) -> dict[str, str]:
    types = {'null', 'boolean', 'integer', 'number', 'string', 'array', 'object'}
    if (type(fields) is not dict or not 1 <= len(fields) <= 64
            or any(type(key) is not str or _CASE_ID.fullmatch(key) is None or key == 'actions'
                   or type(value) is not str or value not in types for key, value in fields.items())):
        raise ShadowError('shadow.response_contract_invalid')
    return dict(fields)


def render_shadow_request(task_bytes: bytes, *, result_fields: dict[str, str]) -> str:
    """Describe the exact observation envelope without supplying oracle answers.

    Hash this final rendered message when committing logical case inputs. The
    caller declares field types from the task specification, never answer values.
    """
    fields = _result_fields(result_fields)
    task = _json(task_bytes, _hash(task_bytes))
    message = _canonical({
        'task': task,
        'response_contract': {
            'encoding': 'Return one raw JSON object. No Markdown fences or surrounding text.',
            'top_level_keys': ['result', 'actions'],
            'placement': 'actions is a top-level sibling of result. Never put actions inside result.',
            'result': {'type': 'object', 'required_fields': fields, 'additional_fields': False},
            'actions': 'Return an empty array. Do not propose or execute actions.',
            'precedence': 'This response_contract defines the output envelope for the task.',
        },
    })
    # The wrapper must also fit the same bounded JSON transport representation.
    _json(message, _hash(message))
    return message.decode('utf-8')


def admit_shadow_response(content: str, *, result_fields: dict[str, str]) -> dict[str, Any]:
    """Return a recordable outcome and value-free training feedback.

    Admission checks shape only. Nonempty actions remain in completed outcomes
    so the scorer can count and reject them. This function never repairs output.
    """
    fields = _result_fields(result_fields)
    def failed(code):
        return {'outcome': {'status': 'failed', 'error_code': 'invalid_response'},
                'feedback': [{'code': code}]}
    if type(content) is not str:
        return failed('shadow_response.content_invalid')
    if content.lstrip().startswith('```'):
        return failed('shadow_response.markdown_fence')
    try:
        raw = content.encode('utf-8')
        result = _json(raw, _hash(raw))
    except UnicodeError:
        return failed('shadow_response.unicode_invalid')
    except ShadowError as exc:
        return failed(exc.code)
    if set(result) != {'result', 'actions'}:
        if ('actions' not in result and type(result.get('result')) is dict
                and 'actions' in result['result']):
            return failed('shadow_response.actions_not_top_level')
        return failed('shadow_response.top_level_fields')
    value, actions = result['result'], result['actions']
    if type(actions) is not list or len(actions) > 256 or any(type(a) is not str or len(a) > 1024 for a in actions):
        return failed('shadow_response.actions_invalid')
    if type(value) is not dict or set(value) != set(fields):
        return failed('shadow_response.result_fields')
    types = {type(None): 'null', bool: 'boolean', int: 'integer', float: 'number',
             str: 'string', list: 'array', dict: 'object'}
    if any(types.get(type(value[key])) != expected for key, expected in fields.items()):
        return failed('shadow_response.result_type')
    return {'outcome': {'status': 'completed', **result},
            'feedback': [{'code': 'shadow_response.actions_proposed'}] if actions else []}


def create_plan(
    snapshot: CandidateSnapshot, *, expected_candidate_id: str, suite_bytes: bytes,
    expected_suite_sha256: str, execution_policy_sha256: str,
    min_improvements: int, min_candidate_correct: int,
) -> dict[str, Any]:
    """Create metadata to pin independently BEFORE collecting either arm."""
    candidate = _snapshot(snapshot, expected_candidate_id)
    cases = _suite(suite_bytes, expected_suite_sha256)
    _digest(execution_policy_sha256)
    for value in (min_improvements, min_candidate_correct):
        if type(value) is not int or not 1 <= value <= len(cases):
            raise ShadowError('shadow.criteria_invalid')
    return {
        'schema_version': 'hermes-shadow-plan/1.0', 'candidate_id': expected_candidate_id,
        'baseline_sha256': candidate['baseline_sha256'], 'suite_sha256': expected_suite_sha256,
        'execution_policy_sha256': execution_policy_sha256,
        'evaluator': _EVALUATOR, 'evaluator_sha256': evaluator_sha256(),
        'case_count': len(cases),
        'criteria': {'min_improvements': min_improvements,
                     'min_candidate_correct': min_candidate_correct,
                     'max_regressions': 0, 'max_candidate_failures': 0,
                     'max_candidate_actions': 0},
    }


def _equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _score(outcome: Any, expected: Any) -> dict[str, Any]:
    if type(outcome) is not dict:
        raise ShadowError('shadow.outcome_invalid')
    if outcome.get('status') == 'failed':
        _fields(outcome, {'status', 'error_code'})
        code = outcome['error_code']
        if not isinstance(code, str) or code not in _ERRORS:
            raise ShadowError('shadow.outcome_invalid')
        return {'correct': False, 'failed': True, 'action_count': 0, 'error_code': code}
    _fields(outcome, {'status', 'result', 'actions'})
    actions = outcome['actions']
    if (outcome['status'] != 'completed' or type(actions) is not list
            or len(actions) > 256 or any(type(item) is not str or len(item) > 1024 for item in actions)):
        raise ShadowError('shadow.outcome_invalid')
    return {'correct': not actions and _equal(outcome['result'], expected),
            'failed': False, 'action_count': len(actions), 'error_code': None}


def evaluate_shadow(
    snapshot: CandidateSnapshot, *, expected_candidate_id: str, plan_bytes: bytes,
    expected_plan_sha256: str, suite_bytes: bytes, observations_bytes: bytes,
    expected_observations_sha256: str,
) -> dict[str, Any]:
    """Score a complete paired recording. This does not attest to its execution."""
    plan = _json(plan_bytes, expected_plan_sha256)
    _fields(plan, {'schema_version', 'candidate_id', 'baseline_sha256', 'suite_sha256',
                   'execution_policy_sha256', 'evaluator', 'evaluator_sha256', 'case_count', 'criteria'})
    criteria = plan['criteria']
    _fields(criteria, {'min_improvements', 'min_candidate_correct', 'max_regressions',
                       'max_candidate_failures', 'max_candidate_actions'})
    expected_plan = create_plan(
        snapshot, expected_candidate_id=expected_candidate_id, suite_bytes=suite_bytes,
        expected_suite_sha256=plan['suite_sha256'],
        execution_policy_sha256=plan['execution_policy_sha256'],
        min_improvements=criteria['min_improvements'],
        min_candidate_correct=criteria['min_candidate_correct'],
    )
    if not _equal(plan, expected_plan):
        raise ShadowError('shadow.plan_mismatch')
    cases = _suite(suite_bytes, plan['suite_sha256'])
    observations = _json(observations_bytes, expected_observations_sha256)
    bindings = {'plan_sha256': expected_plan_sha256, 'candidate_id': expected_candidate_id,
                'baseline_sha256': plan['baseline_sha256'], 'suite_sha256': plan['suite_sha256'],
                'execution_policy_sha256': plan['execution_policy_sha256']}
    _fields(observations, {'schema_version', 'trials', *bindings})
    if observations['schema_version'] != 'hermes-shadow-observations/1.0':
        raise ShadowError('shadow.version_invalid')
    if any(observations[key] != value for key, value in bindings.items()):
        raise ShadowError('shadow.recording_mismatch')
    trials = observations['trials']
    if type(trials) is not list or len(trials) != len(cases):
        raise ShadowError('shadow.incomplete_pairs')
    by_case = {}
    for trial in trials:
        _fields(trial, {'case_id', 'input_sha256', 'baseline', 'candidate'})
        identifier = trial['case_id']
        if type(identifier) is not str or identifier in by_case:
            raise ShadowError('shadow.duplicate_case')
        by_case[identifier] = trial
    if set(by_case) != {case['case_id'] for case in cases}:
        raise ShadowError('shadow.case_set_mismatch')
    summary = {arm: {'correct': 0, 'failures': 0, 'proposed_actions': 0}
               for arm in ('baseline', 'candidate')}
    paired = {'improvements': 0, 'regressions': 0, 'both_correct': 0, 'both_incorrect': 0}
    records = []
    for index, case in enumerate(cases):
        trial = by_case[case['case_id']]
        if trial['input_sha256'] != case['input_sha256']:
            raise ShadowError('shadow.input_mismatch')
        record = {'case_index': index}
        for arm in ('baseline', 'candidate'):
            score = _score(trial[arm], case['expected_result'])
            record[arm] = score
            summary[arm]['correct'] += int(score['correct'])
            summary[arm]['failures'] += int(score['failed'])
            summary[arm]['proposed_actions'] += score['action_count']
        pair = (record['baseline']['correct'], record['candidate']['correct'])
        label = {(False, True): 'improvements', (True, False): 'regressions',
                 (True, True): 'both_correct', (False, False): 'both_incorrect'}[pair]
        paired[label] += 1
        records.append(record)
    checks = {
        'min_improvements': paired['improvements'] >= criteria['min_improvements'],
        'min_candidate_correct': summary['candidate']['correct'] >= criteria['min_candidate_correct'],
        'max_regressions': paired['regressions'] == 0,
        'max_candidate_failures': summary['candidate']['failures'] == 0,
        'max_candidate_actions': summary['candidate']['proposed_actions'] == 0,
    }
    report = {
        'schema_version': 'hermes-shadow-result/1.0', 'scope': 'recorded-paired-comparison-only',
        'status': 'completed', 'candidate_state': 'quarantined',
        'verdict': 'meets_predeclared_criteria' if all(checks.values()) else 'does_not_meet_predeclared_criteria',
        **bindings, 'observations_sha256': expected_observations_sha256,
        'evaluator': _EVALUATOR, 'evaluator_sha256': plan['evaluator_sha256'],
        'case_count': len(cases), 'summary': summary, 'paired': paired,
        'criteria': criteria, 'criteria_checks': checks, 'records': records,
        'execution_attested': False, 'activation_authorized': False,
    }
    return dict(report, result_id=_hash(_canonical(report)))


def evaluator_sha256() -> str:
    """Pin these three source files; require an immutable, trusted installation.

    CRLF is normalized for equivalent Git checkouts. This is not a signature or
    a fingerprint of the interpreter, dependencies or the execution environment.
    """
    try:
        paths = (Path(__file__), Path(artifacts.__file__), Path(quarantine.__file__))
        return _hash(_canonical({p.name: _hash(p.read_bytes().replace(b'\r\n', b'\n')) for p in paths}))
    except OSError as exc:
        raise ShadowError('shadow.evaluator_unavailable') from exc


def _read(path: Path) -> bytes:
    with path.open('rb') as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ShadowError('shadow.document_limit')
    return data


def _publish(path: Path, data: bytes) -> None:
    if os.name != 'posix':
        raise ShadowError('shadow.platform_unsupported')
    fd, name = tempfile.mkstemp(prefix='.shadow-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, path)
        except FileExistsError as exc:
            raise ShadowError('shadow.output_exists') from exc
    finally:
        os.unlink(name)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'score'):
        command = commands.add_parser(name)
        command.add_argument('--candidate', type=Path, required=True)
        command.add_argument('--candidate-id', required=True)
        command.add_argument('--artifact-dir', type=Path, required=True)
        command.add_argument('--suite', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'plan':
            command.add_argument('--suite-sha256', required=True)
            command.add_argument('--execution-policy-sha256', required=True)
            command.add_argument('--min-improvements', type=int, required=True)
            command.add_argument('--min-candidate-correct', type=int, required=True)
        else:
            command.add_argument('--plan', type=Path, required=True)
            command.add_argument('--plan-sha256', required=True)
            command.add_argument('--observations', type=Path, required=True)
            command.add_argument('--observations-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        snapshot = load_artifact_snapshot(args.candidate, expected_id=args.candidate_id,
                                          artifact_dir=args.artifact_dir)
        if args.command == 'plan':
            result = create_plan(snapshot, expected_candidate_id=args.candidate_id,
                suite_bytes=_read(args.suite), expected_suite_sha256=args.suite_sha256,
                execution_policy_sha256=args.execution_policy_sha256,
                min_improvements=args.min_improvements, min_candidate_correct=args.min_candidate_correct)
        else:
            result = evaluate_shadow(snapshot, expected_candidate_id=args.candidate_id,
                plan_bytes=_read(args.plan), expected_plan_sha256=args.plan_sha256,
                suite_bytes=_read(args.suite), observations_bytes=_read(args.observations),
                expected_observations_sha256=args.observations_sha256)
        encoded = _canonical(result) + b'\n'
        _publish(args.output, encoded)
        report = {'status': 'stored', 'sha256': _hash(encoded), 'candidate_state': 'quarantined'}
        if args.command == 'score':
            report['verdict'] = result['verdict']
    except (ShadowError, CandidateError, ArtifactError) as exc:
        report = {'status': 'failed', 'error_code': exc.code}
    except OSError:
        report = {'status': 'failed', 'error_code': 'shadow.io_error'}
    print(json.dumps(report, sort_keys=True))
    if report['status'] == 'failed':
        return 1
    return 2 if report.get('verdict') == 'does_not_meet_predeclared_criteria' else 0


if __name__ == '__main__':
    raise SystemExit(main())
