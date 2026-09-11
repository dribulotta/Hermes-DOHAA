import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning import collection, development as d, review, shadow
from hermes_dohaa.learning.artifacts import ArtifactBytes, CandidateSnapshot, load_artifact_snapshot
from hermes_dohaa.learning.quarantine import build_candidate
from test_shadow_collection import SyntheticAdapter, fixture

digest, encode = shadow._hash, shadow._canonical
REASON = digest(b'Operator-owned isolated development test intention')


@unittest.skipUnless(os.name == 'posix', 'private development selection requires POSIX')
class DevelopmentSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / 'selection'
        self.adapter = SyntheticAdapter()
        self.args = fixture(self.adapter)
        source = self.args['snapshot']
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(mode=0o700)
        for artifact in (source.baseline, *source.evidence):
            (self.artifacts / artifact.sha256).write_bytes(artifact.content)
        self.candidate_path = self.root / 'quarantined.json'
        self.candidate_path.write_bytes(encode(source.candidate.to_dict()))
        self.snapshot = load_artifact_snapshot(self.candidate_path,
            expected_id=source.candidate.candidate_id, artifact_dir=self.artifacts)
        self.args['snapshot'] = self.snapshot
        initial = d.initialize_development_selection(self.snapshot,
            expected_candidate_id=self.args['expected_candidate_id'], output_dir=self.store, reason_sha256=REASON)
        self.head = initial.report()['head_sha256']
        self.initial_record = (self.store / 'state-0.json').read_bytes()

    def tearDown(self):
        self.temporary.cleanup()

    def approval(self, mode='positive', decision='recommend_for_development'):
        self.adapter.mode = mode
        folder = self.root / ('collection-' + mode)
        report = collection.collect_prompt_shadow(**self.args, adapter=self.adapter, output_dir=folder)
        recording = (folder / 'recording.json').read_bytes()
        arguments = dict(self.args, recording_bytes=recording, expected_recording_sha256=digest(recording))
        reviews = self.root / ('reviews-' + mode)
        reviews.mkdir(mode=0o700)
        head = '0' * 64
        if decision is not None:
            head = review.record_collection_review(reviews, audit_arguments=arguments,
                expected_collection_sha256=digest(encode(report)), expected_previous_sha256=head,
                decision=decision, reviewer_sha256=digest(b'test reviewer'), reason_sha256=REASON)['head_sha256']
        return dict(audit_arguments=arguments, expected_collection_sha256=digest(encode(report)),
                    review_directory=reviews, expected_review_head_sha256=head)

    def adopt(self, **approval):
        return d.adopt_development_prompt(self.store, expected_head_sha256=self.head,
                                          reason_sha256=REASON, **approval)

    def assert_baseline(self):
        result = d.read_development_selection(self.store, expected_head_sha256=self.head)
        self.assertEqual(result.prompt, self.snapshot.baseline.content)
        self.assertEqual((self.store/'state-0.json').read_bytes(), self.initial_record)
        self.assertFalse((self.store/'state-1.json').exists())

    def test_baseline_bytes_survive_source_path_mutation(self):
        for path in self.artifacts.iterdir():
            path.write_bytes(b'tampered external source')
        self.candidate_path.write_bytes(b'{}')
        self.assert_baseline()

    def test_adopt_read_and_revert_exact_bytes_without_runtime_or_adapter_calls(self):
        approval = self.approval()
        calls = len(self.adapter.requests)
        adopted = self.adopt(**approval)
        self.assertEqual(adopted.prompt, b'PRIVATE candidate')
        self.assertEqual(adopted.report()['selected'], 'candidate')
        current = adopted.report()['head_sha256']
        self.assertEqual(d.read_development_selection(self.store, expected_head_sha256=current,
            **approval).prompt, adopted.prompt)
        reverted = d.revert_development_prompt(self.store, expected_head_sha256=current, reason_sha256=REASON)
        self.assertEqual(reverted.prompt, self.snapshot.baseline.content)
        self.assertEqual(d.read_development_selection(self.store,
            expected_head_sha256=reverted.report()['head_sha256']).prompt, self.snapshot.baseline.content)
        self.assertEqual(len(self.adapter.requests), calls)
        self.assertEqual((self.store/'state-0.json').read_bytes(), self.initial_record)
        self.assertTrue((self.store/'state-1.json').exists())

    def test_persistent_reverted_selection_read_by_fresh_process(self):
        adopted = self.adopt(**self.approval())
        reverted = d.revert_development_prompt(self.store,
            expected_head_sha256=adopted.report()['head_sha256'], reason_sha256=REASON)
        code = ('import sys,json,hashlib; from pathlib import Path; '
                'from hermes_dohaa.learning.development import read_development_selection; '
                'v=read_development_selection(Path(sys.argv[1]),expected_head_sha256=sys.argv[2]); '
                'print(json.dumps([v.report()["selected"],hashlib.sha256(v.prompt).hexdigest()]))')
        result = subprocess.run([sys.executable, '-c', code, str(self.store), reverted.report()['head_sha256']],
            check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), ['baseline', digest(self.snapshot.baseline.content)])

    def test_negative_evaluation_cannot_be_adopted(self):
        for mode in ('tie', 'failed', 'invalid-response', 'actions'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(d.DevelopmentError, 'development.negative_result'):
                    self.adopt(**self.approval(mode, 'reject'))
                self.assert_baseline()

    def test_positive_but_unreviewed_or_rejected_cannot_be_adopted(self):
        approval = self.approval(decision=None)
        with self.assertRaisesRegex(d.DevelopmentError, 'development.not_recommended'):
            self.adopt(**approval)
        decision = review.record_collection_review(approval['review_directory'],
            audit_arguments=approval['audit_arguments'], expected_collection_sha256=approval['expected_collection_sha256'],
            expected_previous_sha256='0'*64, decision='reject', reviewer_sha256=digest(b'reviewer'), reason_sha256=REASON)
        approval['expected_review_head_sha256'] = decision['head_sha256']
        with self.assertRaisesRegex(d.DevelopmentError, 'development.not_recommended'):
            self.adopt(**approval)
        self.assert_baseline()

    def revoke(self, approval):
        decision = review.record_collection_review(approval['review_directory'],
            audit_arguments=approval['audit_arguments'], expected_collection_sha256=approval['expected_collection_sha256'],
            expected_previous_sha256=approval['expected_review_head_sha256'], decision='revoke',
            reviewer_sha256=digest(b'reviewer'), reason_sha256=REASON)
        return dict(approval, expected_review_head_sha256=decision['head_sha256'])

    def test_revoked_review_denies_adoption_with_old_and_current_review_pins(self):
        approval = self.approval()
        revoked = self.revoke(approval)
        for arguments in (approval, revoked):
            with self.assertRaises((d.DevelopmentError, collection.CollectionError)):
                self.adopt(**arguments)
        self.assert_baseline()

    def test_revocation_denies_future_candidate_reads_but_allows_revert(self):
        approval = self.approval()
        head = self.adopt(**approval).report()['head_sha256']
        revoked = self.revoke(approval)
        for arguments in (approval, revoked):
            with self.assertRaises((d.DevelopmentError, collection.CollectionError)):
                d.read_development_selection(self.store, expected_head_sha256=head, **arguments)
        result = d.revert_development_prompt(self.store, expected_head_sha256=head, reason_sha256=REASON)
        self.assertEqual(result.prompt, self.snapshot.baseline.content)

    def test_selected_candidate_never_returns_without_audit_arguments(self):
        head = self.adopt(**self.approval()).report()['head_sha256']
        with self.assertRaisesRegex(d.DevelopmentError, 'development.audit_required'):
            d.read_development_selection(self.store, expected_head_sha256=head)

    def test_tampered_recording_and_wrong_collection_pin_deny_adoption(self):
        approval = self.approval()
        wrong = dict(approval, expected_collection_sha256='a'*64)
        with self.assertRaises(d.DevelopmentError):
            self.adopt(**wrong)
        wrong = dict(approval, audit_arguments=dict(approval['audit_arguments'], recording_bytes=b'{}'))
        with self.assertRaises(shadow.ShadowError):
            self.adopt(**wrong)
        self.assert_baseline()

    def test_changed_review_bytes_deny_candidate_read(self):
        approval = self.approval()
        head = self.adopt(**approval).report()['head_sha256']
        record = approval['review_directory']/'review-0.json'
        data = json.loads(record.read_bytes())
        data['reason_sha256'] = 'a'*64
        record.write_bytes(encode(data))
        with self.assertRaises(collection.CollectionError):
            d.read_development_selection(self.store, expected_head_sha256=head, **approval)

    def test_tampered_candidate_file_denies_adopt_and_read(self):
        approval = self.approval()
        candidate = self.store/'candidate.bin'
        original = candidate.read_bytes()
        candidate.write_bytes(b'altered')
        with self.assertRaisesRegex(d.DevelopmentError, 'development.candidate_mismatch'):
            self.adopt(**approval)
        candidate.write_bytes(original)
        head = self.adopt(**approval).report()['head_sha256']
        candidate.write_bytes(b'altered again')
        with self.assertRaisesRegex(d.DevelopmentError, 'development.candidate_mismatch'):
            d.read_development_selection(self.store, expected_head_sha256=head, **approval)
        self.assertEqual(d.revert_development_prompt(self.store,
            expected_head_sha256=head, reason_sha256=REASON).prompt, self.snapshot.baseline.content)

    def test_tampered_baseline_denies_reversion(self):
        head = self.adopt(**self.approval()).report()['head_sha256']
        (self.store/'baseline.bin').write_bytes(b'not the original')
        with self.assertRaisesRegex(d.DevelopmentError, 'development.baseline_mismatch'):
            d.revert_development_prompt(self.store, expected_head_sha256=head, reason_sha256=REASON)
        self.assertFalse((self.store/'state-2.json').exists())

    def test_stale_state_cannot_be_read_adopted_or_reverted(self):
        approval = self.approval()
        self.adopt(**approval)
        for operation in (lambda: self.adopt(**approval),
                          lambda: d.read_development_selection(self.store, expected_head_sha256=self.head),
                          lambda: d.revert_development_prompt(self.store, expected_head_sha256=self.head, reason_sha256=REASON)):
            with self.assertRaisesRegex(d.DevelopmentError, 'development.head_mismatch'):
                operation()

    def test_illegal_transitions_do_not_append(self):
        with self.assertRaisesRegex(d.DevelopmentError, 'development.transition_invalid'):
            d.revert_development_prompt(self.store, expected_head_sha256=self.head, reason_sha256=REASON)
        approval = self.approval()
        head = self.adopt(**approval).report()['head_sha256']
        with self.assertRaisesRegex(d.DevelopmentError, 'development.transition_invalid'):
            d.adopt_development_prompt(self.store, expected_head_sha256=head, reason_sha256=REASON, **approval)
        reverted = d.revert_development_prompt(self.store, expected_head_sha256=head, reason_sha256=REASON)
        for operation in ('adopt', 'revert'):
            with self.assertRaisesRegex(d.DevelopmentError, 'development.transition_invalid'):
                arguments = approval if operation == 'adopt' else {}
                getattr(d, operation+'_development_prompt')(self.store,
                    expected_head_sha256=reverted.report()['head_sha256'], reason_sha256=REASON, **arguments)

    def test_truncated_or_extended_chain_is_rejected(self):
        head = self.adopt(**self.approval()).report()['head_sha256']
        record = self.store/'state-1.json'
        original = record.read_bytes()
        record.unlink()
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(self.store, expected_head_sha256=head)
        record.write_bytes(original); record.chmod(0o600)
        extra = self.store/'state-3.json'
        extra.write_bytes(b'{}')
        with self.assertRaisesRegex(d.DevelopmentError, 'development.chain_invalid'):
            d.revert_development_prompt(self.store, expected_head_sha256=head, reason_sha256=REASON)

    def test_chain_tampering_and_forged_authority_flags_rejected_even_with_new_pin(self):
        for key, value in [('sequence', False), ('previous_sha256', 'a'*64),
                           ('activation_authorized', True), ('execution_attested', 0),
                           ('selected', 'candidate'), ('scope', 'production'), ('candidate_state', 'approved')]:
            with self.subTest(field=key):
                data = json.loads(self.initial_record); data[key] = value
                raw = encode(data); (self.store/'state-0.json').write_bytes(raw)
                with self.assertRaises(d.DevelopmentError):
                    d.read_development_selection(self.store, expected_head_sha256=digest(raw))
        (self.store/'state-0.json').write_bytes(self.initial_record)

    def test_symlink_store_file_and_lock_rejected(self):
        alias = self.root/'alias'; alias.symlink_to(self.store, target_is_directory=True)
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(alias, expected_head_sha256=self.head)
        for name in ('baseline.bin', 'state-0.json', 'lock'):
            original = (self.store/name).read_bytes()
            target = self.root/('target-'+name); target.write_bytes(original); target.chmod(0o600)
            (self.store/name).unlink(); (self.store/name).symlink_to(target)
            with self.assertRaises(d.DevelopmentError):
                d.read_development_selection(self.store, expected_head_sha256=self.head)
            (self.store/name).unlink(); (self.store/name).write_bytes(original); (self.store/name).chmod(0o600)

    def test_fifo_and_hardlinked_files_fail_without_blocking(self):
        path = self.store/'baseline.bin'
        original = path.read_bytes(); path.unlink(); os.mkfifo(path, 0o600)
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(self.store, expected_head_sha256=self.head)
        path.unlink(); path.write_bytes(original); path.chmod(0o600)
        os.link(path, self.root/'external-hardlink')
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(self.store, expected_head_sha256=self.head)

    def test_private_modes_and_missing_lock_enforced(self):
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        for path in self.store.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        (self.store/'baseline.bin').chmod(0o644)
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(self.store, expected_head_sha256=self.head)
        (self.store/'baseline.bin').chmod(0o600)
        (self.store/'lock').unlink()
        with self.assertRaises(d.DevelopmentError):
            d.read_development_selection(self.store, expected_head_sha256=self.head)
        self.assertFalse((self.store/'lock').exists())

    def test_two_concurrent_adopters_cannot_replace_or_double_append(self):
        approval = self.approval()
        barrier = threading.Barrier(2)
        def attempt():
            barrier.wait(timeout=10)
            try:
                return self.adopt(**approval).report()['head_sha256']
            except d.DevelopmentError as exc:
                self.assertIn(exc.code, {'development.busy', 'development.head_mismatch'})
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(len(list(self.store.glob('state-*.json'))), 2)
        self.assertEqual(d.read_development_selection(self.store,
            expected_head_sha256=next(x for x in results if x), **approval).prompt, b'PRIVATE candidate')

    def test_another_process_holding_lock_blocks_selection(self):
        code = ('import sys,fcntl; f=open(sys.argv[1],"r+"); '
                'fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.readline()')
        process = subprocess.Popen([sys.executable, '-c', code, str(self.store/'lock')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with self.assertRaisesRegex(d.DevelopmentError, 'development.busy'):
                d.read_development_selection(self.store, expected_head_sha256=self.head)
        finally:
            process.communicate('\n', timeout=10)
        self.assert_baseline()

    def test_publication_failure_preserves_baseline_and_hides_private_error(self):
        approval = self.approval()
        with patch.object(d.os, 'link', side_effect=OSError('SECRET private path')):
            with self.assertRaisesRegex(d.DevelopmentError, '^development.store_io_error$'):
                self.adopt(**approval)
        self.assert_baseline()
        self.assertFalse(list(self.store.glob('.tmp-*')))

    def test_failure_after_commit_requires_external_resolution_not_blind_retry(self):
        approval = self.approval()
        original, calls = os.fsync, 0
        def interrupted(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated commit durability uncertainty')
            return original(fd)
        with patch.object(d.os, 'fsync', side_effect=interrupted):
            with self.assertRaises(d.DevelopmentError):
                self.adopt(**approval)
        self.assertTrue((self.store/'state-1.json').exists())
        with self.assertRaisesRegex(d.DevelopmentError, 'development.head_mismatch'):
            self.adopt(**approval)
        self.assertEqual((self.store/'state-0.json').read_bytes(), self.initial_record)

    def test_reports_and_repr_do_not_disclose_private_bytes_or_claim_activation(self):
        result = self.adopt(**self.approval())
        report = result.report()
        self.assertNotIn('PRIVATE', repr(result) + json.dumps(report))
        self.assertFalse(report['activation_authorized']); self.assertFalse(report['execution_attested'])
        self.assertEqual(report['candidate_state'], 'quarantined')
        report['selected'] = 'modified by caller'
        self.assertEqual(result.report()['selected'], 'candidate')

    def test_initialization_rejects_foreign_id_nonprompt_and_tampered_snapshot(self):
        payload = self.snapshot.candidate.to_dict()['candidate']
        draft = {k:v for k,v in payload.items() if k != 'state'}
        draft['schema_version'] = 'hermes-learning-draft/1.0'; draft['kind'] = 'code_patch'
        candidate = build_candidate(draft)
        alternatives = [(self.snapshot, 'a'*64),
            (CandidateSnapshot(candidate, self.snapshot.baseline, self.snapshot.evidence), candidate.candidate_id),
            (CandidateSnapshot(self.snapshot.candidate, ArtifactBytes(self.snapshot.baseline.sha256, b'tampered'),
                self.snapshot.evidence), self.snapshot.candidate.candidate_id)]
        for snapshot, expected in alternatives:
            with self.assertRaises((d.DevelopmentError, shadow.ShadowError)):
                d.initialize_development_selection(snapshot, expected_candidate_id=expected,
                    output_dir=self.root/'must-not-exist', reason_sha256=REASON)
            self.assertFalse((self.root/'must-not-exist').exists())

    def test_existing_directory_and_nonposix_rejected_without_replacement(self):
        with self.assertRaises(d.DevelopmentError):
            d.initialize_development_selection(self.snapshot, expected_candidate_id=self.snapshot.candidate.candidate_id,
                output_dir=self.store, reason_sha256=REASON)
        with patch.object(d.os, 'name', 'nt'):
            with self.assertRaisesRegex(d.DevelopmentError, 'development.platform_unsupported'):
                d.initialize_development_selection(self.snapshot, expected_candidate_id=self.snapshot.candidate.candidate_id,
                    output_dir=self.root/'windows', reason_sha256=REASON)
        self.assert_baseline()


if __name__ == '__main__':
    unittest.main()
