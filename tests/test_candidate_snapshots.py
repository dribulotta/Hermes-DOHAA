import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning.artifacts import (
    ArtifactError, _read_regular_fd, load_artifact_snapshot, verify_artifacts,
)
from hermes_dohaa.learning.quarantine import CandidateError, build_candidate


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(root, *, baseline=b'{"answer": 42}', evidence=(b'synthetic evidence',),
            artifact='assert 6 * 7 == 42', kind='regression_test'):
    store = root / 'artifacts'
    store.mkdir()
    for data in (baseline, *evidence):
        (store / digest(data)).write_bytes(data)
    candidate = build_candidate({
        'schema_version': 'hermes-learning-draft/1.0', 'kind': kind,
        'baseline_sha256': digest(baseline), 'artifact': artifact,
        'rationale': 'Synthetic snapshot regression.',
        'evidence_sha256': [digest(data) for data in evidence],
    })
    path = root / 'candidate.json'
    path.write_text(json.dumps(candidate.to_dict()), encoding='utf-8')
    return path, candidate.candidate_id, store


def capture(inputs):
    path, candidate_id, store = inputs
    return load_artifact_snapshot(path, expected_id=candidate_id, artifact_dir=store)


class SnapshotStreamTests(unittest.TestCase):
    def test_retained_binary_stream_is_exact_including_empty_artifacts(self):
        for data in (b'', b'\x00\xff\xfe\n' * 17000):
            with self.subTest(size=len(data)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bytes'
                path.write_bytes(data)
                with path.open('rb') as handle:
                    size, _, content = _read_regular_fd(
                        handle.fileno(), digest(data), len(data), retain=True,
                    )
                self.assertEqual(size, len(data))
                self.assertEqual(content, data)
                self.assertIsInstance(content, bytes)

    def test_metadata_only_reader_does_not_retain_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bytes'
            path.write_bytes(b'private bytes')
            with path.open('rb') as handle:
                size, _, content = _read_regular_fd(
                    handle.fileno(), digest(b'private bytes'), 13, retain=False,
                )
            self.assertEqual(size, 13)
            self.assertEqual(content, b'')

    def test_snapshot_on_unsupported_platform_opens_nothing(self):
        with patch('hermes_dohaa.learning.artifacts.os.name', 'nt'), \
                patch('hermes_dohaa.learning.artifacts.read_candidate') as reader, \
                patch('hermes_dohaa.learning.artifacts.os.open') as opened:
            with self.assertRaises(ArtifactError) as raised:
                load_artifact_snapshot('unused', expected_id='a' * 64, artifact_dir='unused')
            self.assertEqual(raised.exception.code, 'artifacts.platform_unsupported')
            reader.assert_not_called()
            opened.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'verified snapshots require POSIX file operations')
class CandidateSnapshotTests(unittest.TestCase):
    def test_round_trip_binds_candidate_and_every_ordered_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory), evidence=(b'first', b'second'))
            path, candidate_id, store = inputs
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (path, *store.iterdir())}
            snapshot = capture(inputs)
            report = snapshot.report()
            metadata = verify_artifacts(path, expected_id=candidate_id, artifact_dir=store)
            self.assertEqual(snapshot.candidate.candidate_id, candidate_id)
            self.assertEqual(snapshot.candidate.to_dict()['candidate']['state'], 'quarantined')
            self.assertEqual(snapshot.baseline.content, b'{"answer": 42}')
            self.assertEqual(tuple(item.content for item in snapshot.evidence), (b'first', b'second'))
            self.assertEqual(report.pop('schema_version'), 'hermes-candidate-snapshot/1.0')
            metadata.pop('schema_version')
            self.assertEqual(report, metadata)
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}, before)
            for item in (snapshot.baseline, *snapshot.evidence):
                self.assertEqual(digest(item.content), item.sha256)

    def test_consumer_uses_verified_bytes_after_source_replacement_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory))
            path, _, store = inputs
            snapshot = capture(inputs)
            original_candidate = snapshot.candidate.to_dict()
            (store / snapshot.baseline.sha256).write_bytes(b'{"answer": -1}')
            (store / snapshot.evidence[0].sha256).unlink()
            path.write_text('replaced candidate', encoding='utf-8')
            # This simple independent data consumer must never reopen the paths.
            with patch('builtins.open', side_effect=AssertionError('consumer reopened a file')), \
                    patch('os.open', side_effect=AssertionError('consumer reopened a descriptor')):
                self.assertEqual(json.loads(snapshot.baseline.content)['answer'], 42)
                self.assertEqual(snapshot.evidence[0].content, b'synthetic evidence')
                self.assertEqual(snapshot.candidate.to_dict(), original_candidate)
                self.assertEqual(snapshot.report()['status'], 'passed')

    def test_result_and_nested_bytes_cannot_be_changed_through_normal_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = capture(fixture(Path(directory)))
            with self.assertRaises(FrozenInstanceError):
                snapshot.baseline = None
            with self.assertRaises(FrozenInstanceError):
                snapshot.baseline.content = b'changed'
            with self.assertRaises(TypeError):
                snapshot.evidence[0] = snapshot.baseline
            candidate_view = snapshot.candidate.to_dict()
            candidate_view['candidate']['state'] = 'promoted'
            report = snapshot.report()
            report['references'][0]['sha256'] = '0' * 64
            self.assertEqual(snapshot.candidate.to_dict()['candidate']['state'], 'quarantined')
            self.assertEqual(snapshot.report()['references'][0]['sha256'], snapshot.baseline.sha256)

    def test_shared_digest_has_one_read_and_one_retained_object_with_both_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory), baseline=b'shared', evidence=(b'shared',))
            with patch('hermes_dohaa.learning.artifacts._read_regular_fd', wraps=_read_regular_fd) as reader:
                snapshot = capture(inputs)
            self.assertEqual(reader.call_count, 1)
            self.assertIs(snapshot.baseline, snapshot.evidence[0])
            self.assertEqual(snapshot.report()['unique_artifacts'], 1)
            self.assertEqual(snapshot.report()['total_bytes'], 6)
            self.assertEqual([x['role'] for x in snapshot.report()['references']], ['baseline', 'evidence'])

    def test_all_candidate_kinds_and_executable_evidence_remain_inert_and_private(self):
        for kind in ('code_patch', 'prompt', 'regression_test'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sentinel = root / 'must-not-exist'
                body = f"__import__('pathlib').Path({str(sentinel)!r}).touch()"
                snapshot = capture(fixture(root, artifact=body, evidence=(body.encode(),), kind=kind))
                self.assertFalse(sentinel.exists())
                rendered = repr(snapshot) + json.dumps(snapshot.report())
                self.assertNotIn(body, rendered)
                self.assertNotIn(str(root), rendered)
                self.assertNotIn('Synthetic snapshot regression.', rendered)
                self.assertEqual(snapshot.candidate.to_dict()['candidate']['artifact'], body)

    def test_wrong_or_rehashed_replacement_candidate_is_rejected_before_store_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path, candidate_id, store = fixture(Path(directory))
            with patch('hermes_dohaa.learning.artifacts.os.open') as opened:
                with self.assertRaises(CandidateError):
                    capture((path, '0' * 64, store))
                opened.assert_not_called()
            draft = json.loads(path.read_text())['candidate']
            draft.pop('state')
            draft['schema_version'] = 'hermes-learning-draft/1.0'
            draft['artifact'] = 'replacement with a new valid internal hash'
            path.write_text(json.dumps(build_candidate(draft).to_dict()))
            with patch('hermes_dohaa.learning.artifacts.os.open') as opened:
                with self.assertRaises(CandidateError):
                    capture((path, candidate_id, store))
                opened.assert_not_called()

    def test_missing_or_wrong_later_evidence_returns_no_snapshot(self):
        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                inputs = fixture(Path(directory), evidence=(b'first', b'last'))
                target = inputs[2] / digest(b'last')
                target.unlink() if missing else target.write_bytes(b'private mismatch')
                with patch('hermes_dohaa.learning.artifacts.CandidateSnapshot') as constructor:
                    with self.assertRaises(ArtifactError) as raised:
                        capture(inputs)
                    constructor.assert_not_called()
                self.assertEqual(raised.exception.code, 'artifacts.missing' if missing else 'artifacts.digest_mismatch')
                self.assertNotIn('private', str(raised.exception))

    def test_memory_capture_obeys_per_file_and_total_unique_byte_limits(self):
        for name, value in (('MAX_ARTIFACT_BYTES', 12), ('MAX_TOTAL_BYTES', 15)):
            with self.subTest(limit=name), tempfile.TemporaryDirectory() as directory:
                inputs = fixture(Path(directory))
                with patch('hermes_dohaa.learning.artifacts.' + name, value), \
                        patch('hermes_dohaa.learning.artifacts.CandidateSnapshot') as constructor:
                    with self.assertRaises(ArtifactError) as raised:
                        capture(inputs)
                    constructor.assert_not_called()
                self.assertEqual(raised.exception.code, 'artifacts.limit_exceeded')

    def test_growth_during_capture_reads_only_one_detection_byte_past_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory), baseline=b'1234', evidence=(b'other',))
            target = inputs[2] / digest(b'1234')
            real_read = os.read
            consumed = 0
            def grow(fd, count):
                nonlocal consumed
                data = real_read(fd, count)
                consumed += len(data)
                if consumed == 4:
                    with target.open('ab') as writer:
                        writer.write(b'x' * 100)
                return data
            with patch('hermes_dohaa.learning.artifacts.MAX_ARTIFACT_BYTES', 4), \
                    patch('hermes_dohaa.learning.artifacts.os.read', side_effect=grow):
                with self.assertRaises(ArtifactError) as raised:
                    capture(inputs)
            self.assertEqual(raised.exception.code, 'artifacts.limit_exceeded')
            self.assertEqual(consumed, 5)

    def test_symlink_directory_fifo_and_symlink_root_fail_closed(self):
        for kind in ('symlink', 'directory', 'fifo', 'root'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path, candidate_id, store = fixture(root)
                target = store / digest(b'synthetic evidence')
                if kind == 'root':
                    alias = root / 'alias'
                    alias.symlink_to(store, target_is_directory=True)
                    store = alias
                else:
                    target.unlink()
                    if kind == 'directory':
                        target.mkdir()
                    elif kind == 'fifo':
                        os.mkfifo(target)
                    else:
                        outside = root / 'outside'
                        outside.write_bytes(b'synthetic evidence')
                        target.symlink_to(outside)
                with self.assertRaises(ArtifactError) as raised:
                    capture((path, candidate_id, store))
                self.assertEqual(raised.exception.code, 'artifacts.directory_invalid' if kind == 'root' else 'artifacts.unsafe_file')

    def test_entry_replacement_after_capture_fails_before_return(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = fixture(root)
            replacement = root / 'replacement'
            replacement.write_bytes(b'{"answer": 42}')
            baseline_hash = digest(replacement.read_bytes())
            def replace(fd, expected, maximum, *, retain):
                result = _read_regular_fd(fd, expected, maximum, retain=retain)
                if expected == baseline_hash:
                    os.replace(replacement, inputs[2] / baseline_hash)
                return result
            with patch('hermes_dohaa.learning.artifacts._read_regular_fd', side_effect=replace):
                with self.assertRaises(ArtifactError) as raised:
                    capture(inputs)
            self.assertEqual(raised.exception.code, 'artifacts.changed')

    def test_early_file_mutation_while_capturing_later_evidence_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory))
            target = inputs[2] / digest(b'{"answer": 42}')
            def mutate(fd, expected, maximum, *, retain):
                result = _read_regular_fd(fd, expected, maximum, retain=retain)
                if expected == digest(b'synthetic evidence'):
                    target.write_bytes(b'changed baseline')
                return result
            with patch('hermes_dohaa.learning.artifacts._read_regular_fd', side_effect=mutate):
                with self.assertRaises(ArtifactError) as raised:
                    capture(inputs)
            self.assertEqual(raised.exception.code, 'artifacts.changed')

    def test_candidate_replacement_during_reference_capture_cannot_change_retained_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = fixture(Path(directory))
            path, candidate_id, _ = inputs
            before = json.loads(path.read_text())
            def replace(fd, expected, maximum, *, retain):
                result = _read_regular_fd(fd, expected, maximum, retain=retain)
                path.write_text('later candidate replacement')
                return result
            with patch('hermes_dohaa.learning.artifacts._read_regular_fd', side_effect=replace):
                snapshot = capture(inputs)
            self.assertEqual(snapshot.candidate.to_dict(), before)
            self.assertEqual(snapshot.candidate.candidate_id, candidate_id)

    def test_all_opened_descriptors_close_after_success_and_read_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                inputs = fixture(Path(directory))
                opened = []
                real_open = os.open
                def record(*args, **kwargs):
                    fd = real_open(*args, **kwargs)
                    opened.append(fd)
                    return fd
                def read(fd, expected, maximum, *, retain):
                    if fail:
                        raise OSError('synthetic interrupted read')
                    return _read_regular_fd(fd, expected, maximum, retain=retain)
                with patch('hermes_dohaa.learning.artifacts.os.open', side_effect=record), \
                        patch('hermes_dohaa.learning.artifacts._read_regular_fd', side_effect=read):
                    if fail:
                        with self.assertRaises(OSError):
                            capture(inputs)
                    else:
                        capture(inputs)
                self.assertGreaterEqual(len(opened), 2)
                for fd in opened:
                    with self.assertRaises(OSError):
                        os.fstat(fd)
