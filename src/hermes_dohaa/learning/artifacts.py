"""Verify candidate references in an operator-controlled, digest-named store."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Sequence

from hermes_dohaa.learning.quarantine import CandidateError, read_candidate


MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_SCHEMA = 'hermes-candidate-artifacts/1.0'
_SCOPE = 'artifact-integrity-only'


class ArtifactError(ValueError):
    """Safe failure code without file paths, contents or computed digests."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _hash_regular_fd(fd: int, expected: str, maximum: int) -> tuple[int, tuple[int, ...]]:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ArtifactError('artifacts.unsafe_file')
    if before.st_size > maximum:
        raise ArtifactError('artifacts.limit_exceeded')
    hashed = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(fd, min(_CHUNK_BYTES, maximum - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > maximum:
            raise ArtifactError('artifacts.limit_exceeded')
        hashed.update(chunk)
    after = os.fstat(fd)
    if _signature(before) != _signature(after) or size != before.st_size:
        raise ArtifactError('artifacts.changed')
    if hashed.hexdigest() != expected:
        raise ArtifactError('artifacts.digest_mismatch')
    return size, _signature(after)


def verify_artifacts(
    candidate_path: str | Path, *, expected_id: str, artifact_dir: str | Path,
) -> dict[str, Any]:
    """Check bytes and identity only; never execute or promote a candidate.

    Directory ancestry must be controlled by the operator. A directory descriptor
    anchors lookups; validated SHA-256 strings are the only relative file names.
    This is not an atomic filesystem snapshot or an authorization certificate.
    """
    if os.name != 'posix' or any(not hasattr(os, flag) for flag in (
        'O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK', 'O_CLOEXEC',
    )):
        raise ArtifactError('artifacts.platform_unsupported')
    candidate = read_candidate(candidate_path, expected_id=expected_id)
    payload = candidate.to_dict()['candidate']
    references = [('baseline', payload['baseline_sha256'])]
    references.extend(('evidence', value) for value in payload['evidence_sha256'])
    try:
        directory_fd = os.open(artifact_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ArtifactError('artifacts.directory_invalid') from exc
    checked: dict[str, tuple[int, tuple[int, ...]]] = {}
    total = 0
    try:
        for _, expected in references:
            if expected in checked:
                continue
            try:
                fd = os.open(expected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=directory_fd)
            except FileNotFoundError as exc:
                raise ArtifactError('artifacts.missing') from exc
            except OSError as exc:
                code = ('artifacts.unsafe_file' if exc.errno in (errno.ELOOP, errno.ENOTDIR)
                        else 'artifacts.io_error')
                raise ArtifactError(code) from exc
            try:
                size, signature = _hash_regular_fd(fd, expected, min(MAX_ARTIFACT_BYTES, MAX_TOTAL_BYTES - total))
            finally:
                os.close(fd)
            total += size
            checked[expected] = (size, signature)

        # Catch entry replacement, including changes to an early file while
        # later references were being read. Subsequent users must reverify.
        for expected, (_, signature) in checked.items():
            try:
                current = os.stat(expected, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise ArtifactError('artifacts.changed') from exc
            if _signature(current) != signature:
                raise ArtifactError('artifacts.changed')
    finally:
        os.close(directory_fd)
    return {
        'schema_version': _SCHEMA, 'status': 'passed', 'scope': _SCOPE,
        'candidate_id': candidate.candidate_id, 'candidate_state': 'quarantined',
        'unique_artifacts': len(checked), 'total_bytes': total,
        'references': [{'role': role, 'sha256': expected, 'size_bytes': checked[expected][0]}
                       for role, expected in references],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--candidate-id', required=True)
    parser.add_argument('--artifact-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify_artifacts(args.candidate, expected_id=args.candidate_id, artifact_dir=args.artifact_dir)
    except (CandidateError, ArtifactError) as exc:
        report = {'schema_version': _SCHEMA, 'status': 'failed', 'scope': _SCOPE, 'error_code': exc.code}
    except OSError:
        report = {'schema_version': _SCHEMA, 'status': 'failed', 'scope': _SCOPE, 'error_code': 'artifacts.io_error'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
