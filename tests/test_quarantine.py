import io
import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning.quarantine import (
    CandidateError, MAX_DOCUMENT_BYTES, build_candidate, freeze_candidate,
    main, read_candidate,
)


def draft():
    return {
        'schema_version': 'hermes-learning-draft/1.0',
        'kind': 'regression_test',
        'baseline_sha256': 'a' * 64,
        'artifact': 'assert 1 + 1 == 2\n',
        'rationale': 'Reproduce a synthetic arithmetic defect.',
        'evidence_sha256': ['b' * 64],
    }


class CandidateTests(unittest.TestCase):
    def test_identity_is_canonical_and_caller_changes_do_not_mutate_candidate(self):
        source = draft()
        candidate = build_candidate(source)
        reordered = dict(reversed(list(source.items())))
        self.assertEqual(candidate.candidate_id, build_candidate(reordered).candidate_id)
        source['evidence_sha256'].append('c' * 64)
        self.assertNotEqual(candidate.candidate_id, build_candidate(source).candidate_id)
        self.assertEqual(candidate.to_dict()['candidate']['state'], 'quarantined')
        view = candidate.to_dict()
        view['candidate']['artifact'] = 'changed'
        self.assertNotEqual(candidate.to_dict(), view)

    def test_invalid_and_authority_bearing_drafts_are_rejected(self):
        variants = []
        for field, value in (
            ('schema_version', 'future/9'), ('kind', 'promote'),
            ('baseline_sha256', '../baseline'), ('artifact', None),
            ('artifact', ''), ('rationale', ' '), ('evidence_sha256', []),
            ('evidence_sha256', ['b' * 64, 'b' * 64]),
            ('evidence_sha256', ['secret invalid digest']),
            ('state', 'promoted'), ('approved', True),
        ):
            value_draft = draft()
            value_draft[field] = value
            variants.append(value_draft)
        missing = draft()
        del missing['baseline_sha256']
        variants.extend([missing, [], None])
        for value in variants:
            with self.subTest(value=value), self.assertRaises(CandidateError):
                build_candidate(value)

    def test_payload_size_is_bounded(self):
        source = draft()
        source['artifact'] = 'x' * MAX_DOCUMENT_BYTES
        with self.assertRaises(CandidateError):
            build_candidate(source)

    def test_reader_detects_tampering_and_requires_external_identity(self):
        original = build_candidate(draft())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            for field, value in (
                ('artifact', 'changed'), ('baseline_sha256', 'c' * 64),
                ('evidence_sha256', ['c' * 64]), ('state', 'promoted'),
            ):
                envelope = original.to_dict()
                envelope['candidate'][field] = value
                path.write_text(json.dumps(envelope), encoding='utf-8')
                with self.subTest(field=field), self.assertRaises(CandidateError):
                    read_candidate(path, expected_id=original.candidate_id)
            replacement = draft()
            replacement['artifact'] = 'changed and rehashed'
            path.write_text(json.dumps(build_candidate(replacement).to_dict()), encoding='utf-8')
            with self.assertRaises(CandidateError):
                read_candidate(path, expected_id=original.candidate_id)

    def test_read_only_verification_does_not_change_file(self):
        candidate = build_candidate(draft())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            path.write_text(json.dumps(candidate.to_dict()), encoding='utf-8')
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.assertEqual(read_candidate(path, expected_id=candidate.candidate_id), candidate)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_duplicate_keys_and_large_documents_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'draft.json'
            for raw in ('{"kind":"prompt","kind":"code_patch"}', ' ' * (MAX_DOCUMENT_BYTES + 1)):
                path.write_text(raw, encoding='utf-8')
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(['freeze', str(path), '--output', str(Path(directory) / 'out.json')])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output.getvalue())['status'], 'failed')
                self.assertFalse((Path(directory) / 'out.json').exists())

    def test_cli_never_prints_invalid_artifact_or_raw_io_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'draft.json'
            source = draft()
            source['artifact'] = 'private candidate body'
            source['approved'] = True
            path.write_text(json.dumps(source), encoding='utf-8')
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['freeze', str(path), '--output', str(Path(directory) / 'out.json')])
            self.assertEqual(code, 1)
            self.assertNotIn('private candidate body', output.getvalue())
            with patch('hermes_dohaa.learning.quarantine.read_candidate', side_effect=OSError('secret endpoint')):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(['verify', str(path), '--candidate-id', 'a' * 64])
            self.assertEqual(code, 1)
            self.assertNotIn('secret', output.getvalue())

    def test_unsupported_platform_fails_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            with patch('hermes_dohaa.learning.quarantine.os.name', 'nt'):
                with self.assertRaises(CandidateError) as raised:
                    freeze_candidate(draft(), path)
            self.assertEqual(raised.exception.code, 'candidate.platform_unsupported')
            self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless(os.name == 'posix', 'private publication requires POSIX permissions')
class CandidatePublicationTests(unittest.TestCase):
    def test_round_trip_is_private_and_does_not_execute_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / 'must-not-exist'
            source = draft()
            source['artifact'] = f"__import__('pathlib').Path({str(sentinel)!r}).touch()"
            path = root / 'candidate.json'
            candidate = freeze_candidate(source, path)
            self.assertEqual(read_candidate(path, expected_id=candidate.candidate_id), candidate)
            self.assertFalse(sentinel.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['candidate.json'])

    def test_existing_file_and_symlink_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / 'existing'
            existing.write_text('original')
            link = root / 'link'
            link.symlink_to(existing)
            for target in (existing, link):
                with self.subTest(target=target), self.assertRaises(CandidateError) as raised:
                    freeze_candidate(draft(), target)
                self.assertEqual(raised.exception.code, 'candidate.exists')
            self.assertEqual(existing.read_text(), 'original')
            self.assertTrue(link.is_symlink())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['existing', 'link'])

    def test_concurrent_writers_publish_one_complete_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            def write(index):
                source = draft()
                source['artifact'] = str(index)
                try:
                    return freeze_candidate(source, path).candidate_id
                except CandidateError as exc:
                    return exc.code
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(write, range(4)))
            winners = [value for value in results if value != 'candidate.exists']
            self.assertEqual(len(winners), 1)
            self.assertEqual(read_candidate(path, expected_id=winners[0]).candidate_id, winners[0])
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_failed_publication_leaves_no_partial_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            with patch('hermes_dohaa.learning.quarantine.os.link', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    freeze_candidate(draft(), path)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_unsynced_file_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            with patch('hermes_dohaa.learning.quarantine.os.fsync', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    freeze_candidate(draft(), path)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cli_freeze_and_verify_only_report_identity_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'draft.json'
            target = root / 'candidate.json'
            source.write_text(json.dumps(draft()), encoding='utf-8')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['freeze', str(source), '--output', str(target)]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(set(report), {'status', 'state', 'candidate_id'})
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['verify', str(target), '--candidate-id', report['candidate_id']]), 0)
            self.assertEqual(json.loads(output.getvalue()), report)
