import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning.artifacts import ArtifactError, _hash_regular_fd, main, verify_artifacts
from hermes_dohaa.learning.quarantine import build_candidate


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(root, shared=False):
    baseline = b'baseline archive'
    evidence = baseline if shared else b'synthetic evidence'
    artifacts = root / 'artifacts'
    artifacts.mkdir()
    for data in (baseline, evidence):
        (artifacts / digest(data)).write_bytes(data)
    candidate = build_candidate({
        'schema_version': 'hermes-learning-draft/1.0',
        'kind': 'regression_test', 'baseline_sha256': digest(baseline),
        'artifact': 'assert 1 + 1 == 2', 'rationale': 'Synthetic regression.',
        'evidence_sha256': [digest(evidence)],
    })
    path = root / 'candidate.json'
    path.write_text(json.dumps(candidate.to_dict()), encoding='utf-8')
    return path, candidate.candidate_id, artifacts, baseline, evidence


class ArtifactStreamTests(unittest.TestCase):
    def test_hashes_actual_bytes_without_modifying_file(self):
        data = b'\x00\xff synthetic bytes\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            path.write_bytes(data)
            before = path.stat().st_mtime_ns
            with path.open('rb') as handle:
                size, _ = _hash_regular_fd(handle.fileno(), digest(data), len(data))
            self.assertEqual(size, len(data))
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_wrong_digest_is_rejected_without_disclosing_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            path.write_bytes(b'private body')
            with path.open('rb') as handle, self.assertRaises(ArtifactError) as raised:
                _hash_regular_fd(handle.fileno(), '0' * 64, 1024)
            self.assertEqual(raised.exception.code, 'artifacts.digest_mismatch')
            self.assertNotIn('private', str(raised.exception))

    def test_oversized_file_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            path.write_bytes(b'12345')
            with path.open('rb') as handle, patch('hermes_dohaa.learning.artifacts.os.read') as read:
                with self.assertRaises(ArtifactError) as raised:
                    _hash_regular_fd(handle.fileno(), digest(b'12345'), 4)
                self.assertEqual(raised.exception.code, 'artifacts.limit_exceeded')
                read.assert_not_called()

    def test_mutation_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            original = b'original bytes'
            path.write_bytes(original)
            original_read = os.read
            changed = False
            def mutate_after_read(fd, count):
                nonlocal changed
                data = original_read(fd, count)
                if data and not changed:
                    changed = True
                    path.write_bytes(b'changed bytes')
                    stamp = path.stat()
                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
                return data
            with path.open('rb') as handle, patch('hermes_dohaa.learning.artifacts.os.read', side_effect=mutate_after_read):
                with self.assertRaises(ArtifactError) as raised:
                    _hash_regular_fd(handle.fileno(), digest(original), 1024)
            self.assertEqual(raised.exception.code, 'artifacts.changed')

    def test_unsupported_platform_opens_no_files(self):
        with patch('hermes_dohaa.learning.artifacts.os.name', 'nt'), patch('hermes_dohaa.learning.artifacts.read_candidate') as read:
            with self.assertRaises(ArtifactError) as raised:
                verify_artifacts('unused', expected_id='a' * 64, artifact_dir='unused')
            self.assertEqual(raised.exception.code, 'artifacts.platform_unsupported')
            read.assert_not_called()

    def test_growth_during_read_cannot_bypass_the_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            original = b'1234'
            path.write_bytes(original)
            original_read = os.read
            consumed = 0
            def append_after_read(fd, count):
                nonlocal consumed
                data = original_read(fd, count)
                consumed += len(data)
                if consumed == len(original):
                    with path.open('ab') as writer:
                        writer.write(b'x' * 4096)
                return data
            with path.open('rb') as handle, patch('hermes_dohaa.learning.artifacts.os.read', side_effect=append_after_read):
                with self.assertRaises(ArtifactError) as raised:
                    _hash_regular_fd(handle.fileno(), digest(original), len(original))
            self.assertEqual(raised.exception.code, 'artifacts.limit_exceeded')
            self.assertEqual(consumed, len(original) + 1)

    def test_cli_withholds_raw_operating_system_errors(self):
        output = io.StringIO()
        with patch('hermes_dohaa.learning.artifacts.verify_artifacts', side_effect=OSError('private path and content')), redirect_stdout(output):
            code = main(['unused', '--candidate-id', 'a' * 64, '--artifact-dir', 'unused'])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'artifacts.io_error')
        self.assertNotIn('private', output.getvalue())


@unittest.skipUnless(os.name == 'posix', 'descriptor-relative verification requires POSIX')
class CandidateArtifactTests(unittest.TestCase):
    def test_complete_verification_binds_candidate_and_keeps_state_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, baseline, evidence = fixture(root)
            files = [path, *artifacts.iterdir()]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
            report = verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['scope'], 'artifact-integrity-only')
            self.assertEqual(report['candidate_id'], candidate_id)
            self.assertEqual(report['candidate_state'], 'quarantined')
            self.assertEqual(report['unique_artifacts'], 2)
            self.assertEqual(report['total_bytes'], len(baseline) + len(evidence))
            self.assertEqual(report['references'], [
                {'role': 'baseline', 'sha256': digest(baseline), 'size_bytes': len(baseline)},
                {'role': 'evidence', 'sha256': digest(evidence), 'size_bytes': len(evidence)},
            ])
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}, before)
            self.assertNotIn(str(root), json.dumps(report))

    def test_wrong_candidate_id_stops_before_artifact_directory_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _, artifacts, _, _ = fixture(Path(directory))
            with patch('hermes_dohaa.learning.artifacts.os.open') as opened:
                with self.assertRaises(ValueError):
                    verify_artifacts(path, expected_id='0' * 64, artifact_dir=artifacts)
                opened.assert_not_called()

    def test_missing_baseline_and_evidence_fail_closed(self):
        for selected in ('baseline', 'evidence'):
            with self.subTest(role=selected), tempfile.TemporaryDirectory() as directory:
                path, candidate_id, artifacts, baseline, evidence = fixture(Path(directory))
                data = baseline if selected == 'baseline' else evidence
                (artifacts / digest(data)).unlink()
                with self.assertRaises(ArtifactError) as raised:
                    verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
                self.assertEqual(raised.exception.code, 'artifacts.missing')

    def test_changed_evidence_does_not_pass_by_its_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path, candidate_id, artifacts, _, evidence = fixture(Path(directory))
            (artifacts / digest(evidence)).write_bytes(b'different bytes')
            with self.assertRaises(ArtifactError) as raised:
                verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(raised.exception.code, 'artifacts.digest_mismatch')

    def test_symlink_file_is_not_followed_outside_the_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, _, evidence = fixture(root)
            outside = root / 'outside'
            outside.write_bytes(evidence)
            target = artifacts / digest(evidence)
            target.unlink()
            target.symlink_to(outside)
            with self.assertRaises(ArtifactError) as raised:
                verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(raised.exception.code, 'artifacts.unsafe_file')

    def test_directory_and_fifo_are_rejected_without_blocking(self):
        for kind in ('directory', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path, candidate_id, artifacts, _, evidence = fixture(Path(directory))
                target = artifacts / digest(evidence)
                target.unlink()
                target.mkdir() if kind == 'directory' else os.mkfifo(target)
                with self.assertRaises(ArtifactError) as raised:
                    verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
                self.assertEqual(raised.exception.code, 'artifacts.unsafe_file')

    def test_symlink_artifact_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, _, _ = fixture(root)
            alias = root / 'alias'
            alias.symlink_to(artifacts, target_is_directory=True)
            with self.assertRaises(ArtifactError) as raised:
                verify_artifacts(path, expected_id=candidate_id, artifact_dir=alias)
            self.assertEqual(raised.exception.code, 'artifacts.directory_invalid')

    def test_per_file_and_total_read_budgets_are_enforced(self):
        for limit_name, value in (('MAX_ARTIFACT_BYTES', 5), ('MAX_TOTAL_BYTES', 20)):
            with self.subTest(limit=limit_name), tempfile.TemporaryDirectory() as directory:
                path, candidate_id, artifacts, _, _ = fixture(Path(directory))
                with patch('hermes_dohaa.learning.artifacts.' + limit_name, value):
                    with self.assertRaises(ArtifactError) as raised:
                        verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
                self.assertEqual(raised.exception.code, 'artifacts.limit_exceeded')

    def test_shared_digest_is_read_once_without_losing_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            path, candidate_id, artifacts, baseline, _ = fixture(Path(directory), shared=True)
            with patch('hermes_dohaa.learning.artifacts._hash_regular_fd', wraps=_hash_regular_fd) as hashed:
                report = verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(hashed.call_count, 1)
            self.assertEqual(report['unique_artifacts'], 1)
            self.assertEqual(report['total_bytes'], len(baseline))
            self.assertEqual([r['role'] for r in report['references']], ['baseline', 'evidence'])

    def test_entry_replacement_after_open_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, baseline, _ = fixture(root)
            replacement = root / 'replacement'
            replacement.write_bytes(baseline)
            replaced = False
            def replace_after_hash(fd, expected, maximum):
                nonlocal replaced
                result = _hash_regular_fd(fd, expected, maximum)
                if not replaced:
                    replaced = True
                    os.replace(replacement, artifacts / digest(baseline))
                return result
            with patch('hermes_dohaa.learning.artifacts._hash_regular_fd', side_effect=replace_after_hash):
                with self.assertRaises(ArtifactError) as raised:
                    verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(raised.exception.code, 'artifacts.changed')

    def test_cli_passes_without_executing_or_exposing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, baseline, _ = fixture(root)
            sentinel = root / 'must-not-exist'
            body = f"__import__('pathlib').Path({str(sentinel)!r}).touch()".encode()
            (artifacts / digest(body)).write_bytes(body)
            source = json.loads(path.read_text())['candidate']
            source.pop('state')
            source['schema_version'] = 'hermes-learning-draft/1.0'
            source['evidence_sha256'] = [digest(body)]
            candidate = build_candidate(source)
            path.write_text(json.dumps(candidate.to_dict()))
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([str(path), '--candidate-id', candidate.candidate_id, '--artifact-dir', str(artifacts)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'passed')
            self.assertFalse(sentinel.exists())
            self.assertNotIn(str(root), output.getvalue())
            self.assertNotIn('__import__', output.getvalue())

    def test_early_artifact_mutation_during_later_verification_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, candidate_id, artifacts, baseline, evidence = fixture(root)
            def mutate_baseline_after_evidence(fd, expected, maximum):
                result = _hash_regular_fd(fd, expected, maximum)
                if expected == digest(evidence):
                    baseline_path = artifacts / digest(baseline)
                    baseline_path.write_bytes(b'changed baseline')
                    stamp = baseline_path.stat()
                    os.utime(baseline_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
                return result
            with patch('hermes_dohaa.learning.artifacts._hash_regular_fd', side_effect=mutate_baseline_after_evidence):
                with self.assertRaises(ArtifactError) as raised:
                    verify_artifacts(path, expected_id=candidate_id, artifact_dir=artifacts)
            self.assertEqual(raised.exception.code, 'artifacts.changed')
