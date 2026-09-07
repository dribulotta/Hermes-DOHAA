"""Persist an isolated development prompt selection; never change a runtime."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import collection, review, shadow
from .artifacts import CandidateSnapshot, _signature

_ZERO = '0' * 64
_SCHEMA = 'hermes-development-selection/1.0'
_SCOPE = 'isolated-development-prompt-selection'
_FIELDS = {'schema_version', 'scope', 'sequence', 'previous_sha256', 'transition',
    'candidate_id', 'baseline_sha256', 'candidate_prompt_sha256', 'selected',
    'collection_sha256', 'review_head_sha256', 'reason_sha256', 'candidate_state',
    'execution_attested', 'activation_authorized'}


class DevelopmentError(ValueError):
    """Value-free failure code; no private paths or prompt text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DevelopmentSelection:
    """Retained bytes for a caller-owned dev consumer, not an authority token."""

    prompt: bytes = field(repr=False)
    _metadata: bytes = field(repr=False)

    def report(self):
        return shadow._json(self._metadata, shadow._hash(self._metadata))


def _private(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise DevelopmentError('development.unsafe_store')


@contextmanager
def _store(path, *, create=False):
    if os.name != 'posix':
        raise DevelopmentError('development.platform_unsupported')
    import fcntl
    directory_fd = lock_fd = None
    try:
        if create:
            Path(path).mkdir(mode=0o700, exist_ok=False)
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _private(os.fstat(directory_fd), directory=True)
        lock_fd = os.open('lock', os.O_RDWR | (os.O_CREAT | os.O_EXCL if create else 0)
                          | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                          0o600, dir_fd=directory_fd)
        _private(os.fstat(lock_fd))
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DevelopmentError('development.busy') from None
        yield directory_fd
    except OSError:
        raise DevelopmentError('development.store_io_error') from None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _read(fd, name, maximum):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    try:
        before = os.fstat(handle)
        _private(before)
        if before.st_size > maximum:
            raise DevelopmentError('development.file_limit')
        parts, count = [], 0
        while True:
            part = os.read(handle, min(65536, maximum - count + 1))
            if not part:
                break
            parts.append(part)
            count += len(part)
            if count > maximum:
                raise DevelopmentError('development.file_limit')
        after = os.fstat(handle)
        if (_signature(before) != _signature(after) or count != before.st_size
                or _signature(after) != _signature(os.stat(name, dir_fd=fd, follow_symlinks=False))):
            raise DevelopmentError('development.file_changed')
        return b''.join(parts)
    finally:
        os.close(handle)


def _publish(fd, name, data):
    """Publish complete bytes exclusively; never replace an existing record."""
    temporary = '.tmp-' + uuid.uuid4().hex
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                     0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)


def _prompts(snapshot, expected_id):
    payload = shadow._snapshot(snapshot, expected_id)
    if payload['kind'] != 'prompt':
        raise DevelopmentError('development.prompt_required')
    baseline = snapshot.baseline.content
    candidate = collection._text(payload['artifact']).encode('utf-8')
    try:
        collection._text(baseline.decode('utf-8'))
    except UnicodeError:
        raise DevelopmentError('development.baseline_invalid') from None
    return baseline, candidate


def _load(fd, expected_head):
    shadow._digest(expected_head)
    names = set(os.listdir(fd))
    fixed = {'lock', 'baseline.bin', 'candidate.bin'}
    records = []
    for index in range(3):
        name = f'state-{index}.json'
        if name not in names:
            break
        data = _read(fd, name, 8192)
        record = shadow._json(data, shadow._hash(data))
        shadow._fields(record, _FIELDS)
        previous = records[-1][0] if records else None
        transition, selected = [('initialize', 'baseline'), ('adopt', 'candidate'), ('revert', 'baseline')][index]
        if (record['schema_version'] != _SCHEMA or record['scope'] != _SCOPE
                or type(record['sequence']) is not int or record['sequence'] != index
                or record['previous_sha256'] != (records[-1][1] if records else _ZERO)
                or record['transition'] != transition or record['selected'] != selected
                or record['candidate_state'] != 'quarantined'
                or record['execution_attested'] is not False or record['activation_authorized'] is not False):
            raise DevelopmentError('development.chain_invalid')
        for key in ('candidate_id', 'baseline_sha256', 'candidate_prompt_sha256', 'reason_sha256'):
            shadow._digest(record[key])
        if index == 0:
            if record['collection_sha256'] is not None or record['review_head_sha256'] is not None:
                raise DevelopmentError('development.chain_invalid')
        else:
            for key in ('collection_sha256', 'review_head_sha256'):
                shadow._digest(record[key])
            stable = ['candidate_id', 'baseline_sha256', 'candidate_prompt_sha256']
            if index == 2:
                stable += ['collection_sha256', 'review_head_sha256']
            if any(record[k] != previous[k] for k in stable):
                raise DevelopmentError('development.chain_invalid')
        records.append((record, shadow._hash(data)))
    if not records or names != fixed | {f'state-{i}.json' for i in range(len(records))}:
        raise DevelopmentError('development.chain_invalid')
    record, head = records[-1]
    if head != expected_head:
        raise DevelopmentError('development.head_mismatch')
    baseline = _read(fd, 'baseline.bin', collection.MAX_TEXT_BYTES)
    if shadow._hash(baseline) != record['baseline_sha256']:
        raise DevelopmentError('development.baseline_mismatch')
    return record, baseline


def _candidate(fd, state):
    data = _read(fd, 'candidate.bin', collection.MAX_TEXT_BYTES)
    if shadow._hash(data) != state['candidate_prompt_sha256']:
        raise DevelopmentError('development.candidate_mismatch')
    return data


def _approval(state, audit_arguments, expected_collection_sha256, review_directory, expected_review_head_sha256):
    for value in (expected_collection_sha256, expected_review_head_sha256):
        shadow._digest(value)
    if type(audit_arguments) is not dict:
        raise DevelopmentError('development.audit_required')
    report = collection.audit_prompt_collection(**audit_arguments)
    if shadow._hash(shadow._canonical(report)) != expected_collection_sha256:
        raise DevelopmentError('development.collection_mismatch')
    if report['result']['verdict'] != 'meets_predeclared_criteria':
        raise DevelopmentError('development.negative_result')
    if report['result']['candidate_id'] != state['candidate_id']:
        raise DevelopmentError('development.candidate_mismatch')
    baseline, candidate = _prompts(audit_arguments['snapshot'], state['candidate_id'])
    if (shadow._hash(baseline) != state['baseline_sha256']
            or shadow._hash(candidate) != state['candidate_prompt_sha256']):
        raise DevelopmentError('development.snapshot_mismatch')
    decision = review.read_collection_reviews(Path(review_directory), collection_report=report,
                                              expected_head_sha256=expected_review_head_sha256)
    if decision['decision'] != 'recommend_for_development':
        raise DevelopmentError('development.not_recommended')


def _result(state, head, prompt):
    return DevelopmentSelection(prompt, shadow._canonical(dict(state, head_sha256=head,
        selected_prompt_sha256=shadow._hash(prompt))))


def initialize_development_selection(snapshot: CandidateSnapshot, *, expected_candidate_id: str,
                                     output_dir: Path, reason_sha256: str) -> DevelopmentSelection:
    """Create a NEW private baseline selection outside runtime paths.

    Caller controls directory ancestry and independently retains the returned
    head before use. This store never writes a runtime profile or executes text.
    """
    shadow._digest(reason_sha256)
    baseline, candidate = _prompts(snapshot, expected_candidate_id)
    state = {'schema_version': _SCHEMA, 'scope': _SCOPE, 'sequence': 0,
        'previous_sha256': _ZERO, 'transition': 'initialize', 'selected': 'baseline',
        'candidate_id': expected_candidate_id, 'baseline_sha256': shadow._hash(baseline),
        'candidate_prompt_sha256': shadow._hash(candidate), 'collection_sha256': None,
        'review_head_sha256': None, 'reason_sha256': reason_sha256,
        'candidate_state': 'quarantined', 'execution_attested': False, 'activation_authorized': False}
    data = shadow._canonical(state)
    with _store(output_dir, create=True) as fd:
        _publish(fd, 'baseline.bin', baseline)
        _publish(fd, 'candidate.bin', candidate)
        _publish(fd, 'state-0.json', data)
    return _result(state, shadow._hash(data), baseline)


def adopt_development_prompt(directory: Path, *, expected_head_sha256: str,
                             audit_arguments: dict, expected_collection_sha256: str,
                             review_directory: Path, expected_review_head_sha256: str,
                             reason_sha256: str) -> DevelopmentSelection:
    """Audit exact collection bytes/review, then exclusively append one adoption."""
    shadow._digest(reason_sha256)
    with _store(directory) as fd:
        state, _ = _load(fd, expected_head_sha256)
        if state['transition'] != 'initialize':
            raise DevelopmentError('development.transition_invalid')
        _approval(state, audit_arguments, expected_collection_sha256, review_directory, expected_review_head_sha256)
        candidate = _candidate(fd, state)
        state = dict(state, sequence=1, previous_sha256=expected_head_sha256,
            transition='adopt', selected='candidate', collection_sha256=expected_collection_sha256,
            review_head_sha256=expected_review_head_sha256, reason_sha256=reason_sha256)
        data = shadow._canonical(state)
        _publish(fd, 'state-1.json', data)
        return _result(state, shadow._hash(data), candidate)


def read_development_selection(directory: Path, *, expected_head_sha256: str,
                                audit_arguments: dict | None = None,
                                expected_collection_sha256: str | None = None,
                                review_directory: Path | None = None,
                                expected_review_head_sha256: str | None = None) -> DevelopmentSelection:
    """Read retained bytes; candidate reads recheck audit and current review pin.

    Caller must serialize review changes with consumption. Separate review and
    selection stores are not a cross-store transaction; returned bytes are a
    point-in-time snapshot, not permission for later or production execution.
    """
    with _store(directory) as fd:
        state, baseline = _load(fd, expected_head_sha256)
        prompt = baseline
        if state['selected'] == 'candidate':
            if audit_arguments is None or review_directory is None:
                raise DevelopmentError('development.audit_required')
            _approval(state, audit_arguments, expected_collection_sha256, review_directory, expected_review_head_sha256)
            if (state['collection_sha256'] != expected_collection_sha256
                    or state['review_head_sha256'] != expected_review_head_sha256):
                raise DevelopmentError('development.approval_mismatch')
            prompt = _candidate(fd, state)
        return _result(state, expected_head_sha256, prompt)


def revert_development_prompt(directory: Path, *, expected_head_sha256: str,
                              reason_sha256: str) -> DevelopmentSelection:
    """Permanently close this selection on the exact retained baseline.

    Reversion does not require a still-positive review or intact candidate file.
    It does require an intact externally pinned state chain and baseline bytes.
    """
    shadow._digest(reason_sha256)
    with _store(directory) as fd:
        state, baseline = _load(fd, expected_head_sha256)
        if state['transition'] != 'adopt':
            raise DevelopmentError('development.transition_invalid')
        state = dict(state, sequence=2, previous_sha256=expected_head_sha256,
            transition='revert', selected='baseline', reason_sha256=reason_sha256)
        data = shadow._canonical(state)
        _publish(fd, 'state-2.json', data)
        return _result(state, shadow._hash(data), baseline)
