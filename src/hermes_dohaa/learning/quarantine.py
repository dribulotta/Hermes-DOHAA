"""Freeze and verify untrusted candidates without executing or promoting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_DRAFT_SCHEMA = 'hermes-learning-draft/1.0'
_CANDIDATE_SCHEMA = 'hermes-learning-candidate/1.0'
_FIELDS = {'schema_version', 'kind', 'baseline_sha256', 'artifact', 'rationale', 'evidence_sha256'}
_KINDS = {'code_patch', 'prompt', 'regression_test'}
_SHA256 = re.compile(r'[0-9a-f]{64}')


class CandidateError(ValueError):
    """A safe, stable failure code without candidate contents."""

    def __init__(self, code: str = 'candidate.invalid') -> None:
        self.code = code
        super().__init__(code)


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CandidateError() from exc


@dataclass(frozen=True, slots=True)
class Candidate:
    """An immutable snapshot; returned dictionaries are independent copies."""

    _payload: bytes = field(repr=False)

    @property
    def candidate_id(self) -> str:
        return hashlib.sha256(self._payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {'candidate_id': self.candidate_id, 'candidate': json.loads(self._payload)}


def build_candidate(draft: Any) -> Candidate:
    """Bind artifact, baseline and evidence to one quarantined identity."""
    if not isinstance(draft, dict) or set(draft) != _FIELDS:
        raise CandidateError()
    if draft['schema_version'] != _DRAFT_SCHEMA:
        raise CandidateError()
    if not isinstance(draft['kind'], str) or draft['kind'] not in _KINDS:
        raise CandidateError()
    if not _is_digest(draft['baseline_sha256']):
        raise CandidateError()
    for name in ('artifact', 'rationale'):
        if not isinstance(draft[name], str) or not draft[name].strip():
            raise CandidateError()
    if len(draft['rationale']) > 8192:
        raise CandidateError()
    evidence = draft['evidence_sha256']
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 32
            or not all(_is_digest(item) for item in evidence)
            or len(set(evidence)) != len(evidence)):
        raise CandidateError()
    payload = dict(draft, schema_version=_CANDIDATE_SCHEMA, state='quarantined')
    candidate = Candidate(_canonical(payload))
    if len(_canonical(candidate.to_dict())) + 1 > MAX_DOCUMENT_BYTES:
        raise CandidateError('candidate.too_large')
    return candidate


def freeze_candidate(draft: Any, output: str | Path) -> Candidate:
    """Publish a private complete file once, in an existing trusted directory.

    A hard link atomically claims the final name after the temporary file is
    synced. Concurrent writers cannot replace it. This is application-level
    immutability, not protection against the filesystem owner or administrator.
    """
    candidate = build_candidate(draft)
    if os.name != 'posix':
        raise CandidateError('candidate.platform_unsupported')
    target = Path(output)
    fd, temporary_name = tempfile.mkstemp(prefix='.candidate-', dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(_canonical(candidate.to_dict()) + b'\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise CandidateError('candidate.exists') from exc
    finally:
        temporary.unlink(missing_ok=True)
    directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return candidate


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CandidateError()
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise CandidateError()


def _load_document(path: str | Path) -> Any:
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise CandidateError('candidate.too_large')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CandidateError):
            raise
        raise CandidateError() from exc


def read_candidate(path: str | Path, *, expected_id: str) -> Candidate:
    """Verify against an ID retained independently, without writing anything."""
    if not _is_digest(expected_id):
        raise CandidateError()
    envelope = _load_document(path)
    if (not isinstance(envelope, dict)
            or set(envelope) != {'candidate_id', 'candidate'}
            or envelope['candidate_id'] != expected_id):
        raise CandidateError('candidate.integrity_failed')
    payload = envelope['candidate']
    if (not isinstance(payload, dict) or set(payload) != _FIELDS | {'state'}
            or payload['schema_version'] != _CANDIDATE_SCHEMA
            or payload['state'] != 'quarantined'):
        raise CandidateError('candidate.integrity_failed')
    draft = {key: value for key, value in payload.items() if key != 'state'}
    draft['schema_version'] = _DRAFT_SCHEMA
    candidate = build_candidate(draft)
    if candidate.candidate_id != expected_id:
        raise CandidateError('candidate.integrity_failed')
    return candidate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('freeze', help='Store an untrusted draft once; POSIX only')
    freeze.add_argument('draft', type=Path)
    freeze.add_argument('--output', type=Path, required=True)
    verify = commands.add_parser('verify', help='Verify a candidate against its retained ID')
    verify.add_argument('candidate', type=Path)
    verify.add_argument('--candidate-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            candidate = freeze_candidate(_load_document(args.draft), args.output)
        else:
            candidate = read_candidate(args.candidate, expected_id=args.candidate_id)
        report = {'status': 'passed', 'state': 'quarantined', 'candidate_id': candidate.candidate_id}
    except CandidateError as exc:
        report = {'status': 'failed', 'error_code': exc.code}
    except OSError:
        report = {'status': 'failed', 'error_code': 'candidate.io_error'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
