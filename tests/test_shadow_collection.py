import base64
import copy
import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning import collection as c, shadow
from hermes_dohaa.learning.artifacts import ArtifactBytes, CandidateSnapshot
from hermes_dohaa.learning.quarantine import build_candidate
from hermes_dohaa.learning.review import read_collection_reviews, record_collection_review

encode = shadow._canonical
digest = shadow._hash
LIFECYCLE = encode({'schema_version': 'hermes-shadow-lifecycle/1.0',
                    'idle': True, 'models_unloaded': True})


class SyntheticAdapter:
    """No network, tools, model, external process or candidate-code execution."""
    runtime_policy_sha256 = digest(b'synthetic-runtime-policy')

    def __init__(self, directory=None):
        self.requests = []
        self.starts = self.finishes = 0
        self.mode = 'positive'
        self.directory = directory

    def start(self):
        self.starts += 1
        if self.mode == 'start-uncertain':
            return encode({'schema_version': 'hermes-shadow-lifecycle/1.0',
                           'idle': False, 'models_unloaded': False})
        return LIFECYCLE

    def finish(self):
        self.finishes += 1
        if self.mode == 'finish-uncertain':
            raise RuntimeError('SECRET backend details')
        return LIFECYCLE

    def generate(self, request_bytes):
        if self.directory is not None:
            index = len(self.requests)
            # Actual disk evidence must exist BEFORE entry into the adapter.
            path = self.directory / f'{2 + 2 * index:04d}.json'
            assert json.loads(path.read_bytes())['payload']['request'].encode() == request_bytes
        self.requests.append(request_bytes)
        request = json.loads(request_bytes)
        if self.mode == 'raise':
            raise RuntimeError('SECRET endpoint/password/error')
        if self.mode == 'forged-error':
            raise shadow.ShadowError('SECRET disguised diagnostic')
        if self.mode == 'interrupt':
            raise KeyboardInterrupt()
        if self.mode == 'invalid-utf8':
            return b'\xff\xfe'
        if self.mode == 'oversized':
            return b'x' * (8 * c.MAX_TEXT_BYTES + 1)
        answer = int(request['input'].split(':')[-1])
        if request['prompt'] == 'PRIVATE baseline' and self.mode not in {'tie', 'actions'}:
            answer += 1
        envelope = {'result': {'answer': answer}, 'actions': []}
        if self.mode == 'actions':
            envelope['actions'] = ['SECRET touch /should-never-exist']
        receipt = {'schema_version': 'hermes-shadow-terminal/1.0',
                   'request_sha256': digest(request_bytes), 'server_finished': True,
                   'status': 'completed', 'content': json.dumps(envelope)}
        if self.mode == 'failed':
            receipt.pop('content')
            receipt.update(status='failed', error_code='budget_exhausted')
        if self.mode == 'invalid-response':
            receipt['content'] = '```json\n' + receipt['content'] + '\n```'
        if self.mode == 'uncertain':
            receipt['server_finished'] = False
        if self.mode == 'wrong-request':
            receipt['request_sha256'] = 'a' * 64
        return encode(receipt)


def fixture(adapter):
    baseline, evidence = b'PRIVATE baseline', b'PRIVATE training evidence NEVER disclose'
    candidate = build_candidate({'schema_version': 'hermes-learning-draft/1.0',
        'kind': 'prompt', 'baseline_sha256': digest(baseline), 'artifact': 'PRIVATE candidate',
        'rationale': 'PRIVATE rationale', 'evidence_sha256': [digest(evidence)]})
    snapshot = CandidateSnapshot(candidate, ArtifactBytes(digest(baseline), baseline),
                                 (ArtifactBytes(digest(evidence), evidence),))
    inputs = {'caseA': 'Public input:7', 'caseB': 'Public input:13'}
    suite = {'schema_version': 'hermes-shadow-suite/1.0', 'cases': [
        {'case_id': key, 'input_sha256': digest(value.encode()),
         'expected_result': {'answer': int(value.split(':')[-1])}}
        for key, value in inputs.items()]}
    policy = encode(c.create_collection_policy(adapter_sha256=c.adapter_source_sha256(adapter),
        runtime_policy_sha256=adapter.runtime_policy_sha256, result_fields={'answer': 'integer'}))
    plan = encode(shadow.create_plan(snapshot, expected_candidate_id=candidate.candidate_id,
        suite_bytes=encode(suite), expected_suite_sha256=digest(encode(suite)),
        execution_policy_sha256=digest(policy), min_improvements=1, min_candidate_correct=2))
    return dict(snapshot=snapshot, expected_candidate_id=candidate.candidate_id, plan_bytes=plan,
                expected_plan_sha256=digest(plan), suite_bytes=encode(suite), policy_bytes=policy,
                inputs_bytes=encode({'schema_version': 'hermes-shadow-inputs/1.0', 'inputs': inputs}))


def rebuild_chain(raw):
    previous = '0' * 64
    for sequence, event in enumerate(raw['events']):
        event['sequence'], event['previous_sha256'] = sequence, previous
        previous = digest(encode(event))
    return encode(raw)


@unittest.skipUnless(os.name == 'posix', 'private publication requires POSIX')
class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'run'
        self.adapter = SyntheticAdapter(self.directory)
        self.args = fixture(self.adapter)

    def tearDown(self):
        self.temporary.cleanup()

    def collect(self):
        return c.collect_prompt_shadow(**self.args, adapter=self.adapter, output_dir=self.directory)

    def audit_args(self, recording=None):
        recording = recording if recording is not None else (self.directory / 'recording.json').read_bytes()
        return dict(self.args, recording_bytes=recording, expected_recording_sha256=digest(recording))

    def test_actual_invocation_order_durable_dispatch_and_independent_reconstruction(self):
        report = self.collect()
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report, c.audit_prompt_collection(**self.audit_args()))
        self.assertEqual(report['result']['summary']['candidate']['correct'], 2)
        self.assertEqual(report['result']['paired']['improvements'], 2)
        self.assertEqual(report['request_count'], 4)
        self.assertEqual([json.loads(r)['prompt'] for r in self.adapter.requests],
                         ['PRIVATE baseline', 'PRIVATE candidate', 'PRIVATE candidate', 'PRIVATE baseline'])
        self.assertEqual(self.adapter.starts, 1)
        self.assertEqual(self.adapter.finishes, 1)
        self.assertEqual(len({json.loads(r)['request_id'] for r in self.adapter.requests}), 4)
        self.assertFalse(report['execution_attested'])
        self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['result']['execution_attested'])

    def test_adapter_never_receives_oracles_evidence_case_names_or_feedback(self):
        self.collect()
        for data in self.adapter.requests:
            request = json.loads(data)
            self.assertEqual(set(request), {'schema_version', 'request_id', 'input_sha256',
                'prompt_sha256', 'execution_policy_sha256', 'input', 'prompt'})
            for private in ('expected_result', 'caseA', 'caseB', 'rationale', 'evidence', 'feedback', 'SECRET'):
                self.assertNotIn(private, data.decode())

    def test_tie_is_retained_negative_and_does_not_trigger_retries(self):
        self.adapter.mode = 'tie'
        result = self.collect()
        self.assertEqual(result['result']['verdict'], 'does_not_meet_predeclared_criteria')
        self.assertEqual(len(self.adapter.requests), 4)
        self.assertTrue((self.directory / 'result.json').is_file())

    def test_confirmed_runtime_failures_stay_in_denominator(self):
        self.adapter.mode = 'failed'
        report = self.collect()
        self.assertEqual(report['result']['summary']['candidate']['failures'], 2)
        self.assertEqual(report['result']['case_count'], 2)
        self.assertEqual(len(self.adapter.requests), 4)
        self.assertEqual(self.adapter.finishes, 1)

    def test_strict_admission_failure_is_retained_without_repair(self):
        self.adapter.mode = 'invalid-response'
        report = self.collect()
        self.assertEqual(report['result']['summary']['candidate']['failures'], 2)
        receipt = json.loads((self.directory / '0003.json').read_bytes())['payload']['receipt_base64']
        self.assertIn('```json', base64.b64decode(receipt).decode())
        self.assertEqual(report['result']['records'][0]['candidate']['error_code'], 'invalid_response')

    def test_actions_are_never_executed_and_are_counted(self):
        self.adapter.mode = 'actions'
        report = self.collect()
        self.assertEqual(report['result']['summary']['candidate']['proposed_actions'], 2)
        self.assertEqual(report['result']['summary']['candidate']['correct'], 0)
        self.assertNotIn('SECRET', json.dumps(report))

    def test_unknown_completion_exception_interrupt_and_bad_receipt_stop_without_unload(self):
        for mode in ('uncertain', 'raise', 'forged-error', 'interrupt', 'wrong-request', 'invalid-utf8', 'oversized'):
            with self.subTest(mode=mode):
                self.directory = self.root / mode
                self.adapter = SyntheticAdapter(self.directory)
                self.adapter.mode = mode
                self.args = fixture(self.adapter)
                result = self.collect()
                self.assertEqual(result['status'], 'aborted')
                self.assertTrue(result['server_state_uncertain'])
                self.assertFalse(result['cleanup_confirmed'])
                self.assertEqual(len(self.adapter.requests), 1)
                self.assertEqual(self.adapter.finishes, 0)
                self.assertFalse((self.directory / 'result.json').exists())
                self.assertNotIn('SECRET', json.dumps(result))
                if mode == 'invalid-utf8':
                    event = json.loads((self.directory / '0003.json').read_bytes())
                    self.assertEqual(base64.b64decode(event['payload']['receipt_base64']), b'\xff\xfe')

    def test_bad_start_does_not_generate_or_unload(self):
        self.adapter.mode = 'start-uncertain'
        report = self.collect()
        self.assertEqual(report['status'], 'aborted')
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.adapter.finishes, 0)

    def test_cleanup_failure_cannot_publish_a_success(self):
        self.adapter.mode = 'finish-uncertain'
        report = self.collect()
        self.assertEqual(report['status'], 'aborted')
        self.assertTrue(report['server_state_uncertain'])
        self.assertEqual(len(self.adapter.requests), 4)
        self.assertEqual(self.adapter.finishes, 1)
        self.assertFalse((self.directory / 'recording.json').exists())

    def test_mismatched_inputs_policy_and_plan_fail_before_adapter_or_directory(self):
        for field in ('inputs_bytes', 'policy_bytes', 'plan_bytes', 'suite_bytes'):
            with self.subTest(field=field):
                arguments = dict(self.args, **{field: b'{}'})
                with self.assertRaises(shadow.ShadowError):
                    c.collect_prompt_shadow(**arguments, adapter=self.adapter, output_dir=self.directory)
                self.assertFalse(self.directory.exists())
                self.assertEqual(self.adapter.starts, 0)

    def test_adapter_identity_and_runtime_config_mismatch_fail_before_start(self):
        self.adapter.runtime_policy_sha256 = digest(b'changed')
        with self.assertRaisesRegex(c.CollectionError, 'adapter_mismatch'):
            self.collect()
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.adapter.starts, 0)

    def test_no_resume_no_overwrite_and_private_modes(self):
        self.collect()
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual(self.adapter.starts, 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for p in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_changed_adapter_source_is_rejected_before_start(self):
        with patch.object(c, 'adapter_source_sha256', return_value='f' * 64):
            with self.assertRaisesRegex(c.CollectionError, 'adapter_mismatch'):
                self.collect()
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.adapter.starts, 0)

    def test_relaxed_policy_rejected_even_when_plan_commits_new_digest(self):
        for key, value in (('concurrency', 4), ('retries', 1), ('order', 'candidate-first'),
                           ('max_text_bytes', c.MAX_TEXT_BYTES + 1)):
            with self.subTest(key=key):
                args = dict(self.args)
                policy = json.loads(args['policy_bytes'])
                policy[key] = value
                args['policy_bytes'] = encode(policy)
                plan = json.loads(args['plan_bytes'])
                plan['execution_policy_sha256'] = digest(args['policy_bytes'])
                args['plan_bytes'] = encode(plan)
                args['expected_plan_sha256'] = digest(args['plan_bytes'])
                with self.assertRaisesRegex(c.CollectionError, 'policy_mismatch'):
                    c.collect_prompt_shadow(**args, adapter=self.adapter, output_dir=self.directory)
                self.assertEqual(self.adapter.starts, 0)

    def test_journal_budget_stops_before_next_dispatch_and_keeps_abort(self):
        append = c._Journal.append
        def exhausted(journal, kind, payload, **kwargs):
            if kind == 'dispatch' and len(self.adapter.requests) == 1:
                raise c.CollectionError('collection.recording_limit')
            return append(journal, kind, payload, **kwargs)
        with patch.object(c._Journal, 'append', exhausted):
            report = self.collect()
        self.assertEqual(report['status'], 'aborted')
        self.assertEqual(report['error_code'], 'collection.recording_limit')
        self.assertEqual(len(self.adapter.requests), 1)
        self.assertEqual(self.adapter.finishes, 1)
        self.assertTrue(report['cleanup_confirmed'])
        self.assertFalse(report['server_state_uncertain'])
        self.assertFalse((self.directory / 'result.json').exists())

    def test_distinct_runs_have_distinct_session_identifiers(self):
        self.collect()
        first = {json.loads(r)['request_id'] for r in self.adapter.requests}
        second_dir = self.root / 'second'
        other = SyntheticAdapter(second_dir)
        c.collect_prompt_shadow(**self.args, adapter=other, output_dir=second_dir)
        self.assertFalse(first & {json.loads(r)['request_id'] for r in other.requests})

    def test_storage_failure_before_dispatch_prevents_call_and_finishes_known_idle(self):
        publish = shadow._publish
        def fail(path, data):
            if path.name == '0002.json':
                raise OSError('SECRET storage')
            return publish(path, data)
        with patch.object(shadow, '_publish', side_effect=fail):
            # The abort cannot be made durable either; the error must propagate.
            with self.assertRaises(OSError):
                self.collect()
        self.assertEqual(self.adapter.requests, [])
        self.assertEqual(self.adapter.finishes, 1)
        self.assertFalse((self.directory / 'result.json').exists())

    def test_changed_recording_external_digest_rejected(self):
        self.collect()
        args = self.audit_args()
        args['recording_bytes'] += b' '
        with self.assertRaisesRegex(shadow.ShadowError, 'digest_mismatch'):
            c.audit_prompt_collection(**args)

    def test_rechained_substitution_reordering_missing_and_duplicate_receipts_rejected(self):
        self.collect()
        original = json.loads((self.directory / 'recording.json').read_bytes())
        variants = []
        raw = copy.deepcopy(original)
        raw['events'][2]['payload']['request'] = raw['events'][4]['payload']['request']
        variants.append(raw)
        raw = copy.deepcopy(original)
        raw['events'][3]['payload'] = raw['events'][5]['payload']
        variants.append(raw)
        raw = copy.deepcopy(original)
        raw['events'][2:6] = raw['events'][4:6] + raw['events'][2:4]
        variants.append(raw)
        raw = copy.deepcopy(original)
        raw['events'].pop(3)
        variants.append(raw)
        raw = copy.deepcopy(original)
        raw['events'].append(copy.deepcopy(raw['events'][-1]))
        variants.append(raw)
        for raw in variants:
            with self.subTest(index=variants.index(raw)):
                with self.assertRaises(shadow.ShadowError):
                    c.audit_prompt_collection(**self.audit_args(rebuild_chain(raw)))

    def test_chain_manifest_lifecycle_and_extra_fields_fail_closed(self):
        self.collect()
        original = json.loads((self.directory / 'recording.json').read_bytes())
        for mutation in ('chain', 'manifest', 'lifecycle', 'fields'):
            raw = copy.deepcopy(original)
            if mutation == 'chain':
                raw['events'][3]['previous_sha256'] = 'f' * 64
                data = encode(raw)
            else:
                if mutation == 'manifest':
                    raw['events'][0]['payload']['inputs_sha256'] = 'f' * 64
                if mutation == 'lifecycle':
                    raw['events'][-1]['payload'] = c._capture(encode({'idle': True}))
                if mutation == 'fields':
                    raw['events'][2]['payload']['oracle'] = 7
                data = rebuild_chain(raw)
            with self.subTest(mutation=mutation), self.assertRaises(shadow.ShadowError):
                c.audit_prompt_collection(**self.audit_args(data))

    def test_safe_report_omits_raw_values_inputs_prompts_actions_and_case_ids(self):
        report = self.collect()
        text = json.dumps(report)
        for private in ('PRIVATE', 'caseA', 'caseB', 'Public input', 'answer', 'expected_result', 'prompt'):
            self.assertNotIn(private, text)

    def review(self, report, decision, previous='0' * 64):
        return record_collection_review(self.directory, audit_arguments=self.audit_args(),
            expected_collection_sha256=digest(encode(report)), expected_previous_sha256=previous,
            decision=decision, reviewer_sha256=digest(b'private authorization record'),
            reason_sha256=digest(b'private review reasoning'))

    def test_recommendation_then_revocation_preserves_all_evidence_and_grants_no_activation(self):
        report = self.collect()
        original = (self.directory / 'recording.json').read_bytes()
        recommended = self.review(report, 'recommend_for_development')
        revoked = self.review(report, 'revoke', recommended['head_sha256'])
        self.assertEqual(revoked['decision'], 'revoke')
        self.assertFalse(revoked['activation_authorized'])
        self.assertEqual(original, (self.directory / 'recording.json').read_bytes())
        state = read_collection_reviews(self.directory, collection_report=report,
                                         expected_head_sha256=revoked['head_sha256'])
        self.assertEqual(state['count'], 2)
        with self.assertRaisesRegex(c.CollectionError, 'transition_invalid'):
            self.review(report, 'recommend_for_development', revoked['head_sha256'])

    def test_negative_collection_can_be_rejected_but_never_recommended(self):
        self.adapter.mode = 'tie'
        report = self.collect()
        with self.assertRaisesRegex(c.CollectionError, 'negative_result'):
            self.review(report, 'recommend_for_development')
        self.assertFalse((self.directory / 'review-0.json').exists())
        rejected = self.review(report, 'reject')
        with self.assertRaisesRegex(c.CollectionError, 'transition_invalid'):
            self.review(report, 'revoke', rejected['head_sha256'])

    def test_review_wrong_collection_and_stale_head_rejected(self):
        report = self.collect()
        changed = copy.deepcopy(report)
        changed['request_count'] = 99
        with self.assertRaisesRegex(c.CollectionError, 'collection_mismatch'):
            self.review(changed, 'recommend_for_development')
        self.review(report, 'recommend_for_development')
        with self.assertRaisesRegex(c.CollectionError, 'head_mismatch'):
            self.review(report, 'revoke')

    def test_review_chain_removal_extra_file_and_symlink_are_not_accepted(self):
        report = self.collect()
        recommended = self.review(report, 'recommend_for_development')
        path = self.directory / 'review-0.json'
        data = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(c.CollectionError, 'head_mismatch'):
            read_collection_reviews(self.directory, collection_report=report,
                                     expected_head_sha256=recommended['head_sha256'])
        path.write_bytes(data)
        extra = self.directory / 'review-2.json'
        extra.write_bytes(data)
        with self.assertRaisesRegex(c.CollectionError, 'chain_invalid'):
            self.review(report, 'revoke', recommended['head_sha256'])
        extra.unlink()
        moved = self.directory / 'original-review.json'
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaisesRegex(c.CollectionError, 'file_invalid'):
            self.review(report, 'revoke', recommended['head_sha256'])

    def test_concurrent_review_writers_cannot_both_claim_first_decision(self):
        report = self.collect()
        def attempt(_):
            try:
                self.review(report, 'recommend_for_development')
                return True
            except shadow.ShadowError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)
        self.assertTrue((self.directory / 'review-0.json').is_file())
        self.assertFalse((self.directory / 'review-1.json').exists())


class CollectionPortableTests(unittest.TestCase):
    def test_capture_preserves_bytes_and_rejects_noncanonical_or_oversized_data(self):
        self.assertEqual(c._uncapture(c._capture(b'\xff invalid JSON')), b'\xff invalid JSON')
        for value in (b'x' * (8 * c.MAX_TEXT_BYTES + 1), 'text instead of bytes'):
            with self.assertRaises(c.CollectionError):
                c._capture(value)
        with self.assertRaises(c.CollectionError):
            c._uncapture({'receipt_base64': 'eA==\n'})

    def test_actual_journal_byte_budget_preserves_space_for_abort(self):
        journal = object.__new__(c._Journal)
        journal.directory, journal.events, journal.head = Path('.'), [], '0' * 64
        journal.size = c.MAX_RECORDING_BYTES - 16384
        with patch.object(shadow, '_publish') as publish:
            with self.assertRaisesRegex(c.CollectionError, 'recording_limit'):
                journal.append('dispatch', {'request': 'bounded'})
            publish.assert_not_called()
            journal.append('aborted', {'status': 'aborted'}, emergency=True)
            publish.assert_called_once()

    def test_policy_pins_source_and_rejects_invalid_field_declaration(self):
        adapter = SyntheticAdapter()
        args = fixture(adapter)
        policy = json.loads(args['policy_bytes'])
        self.assertEqual(policy['collector_sha256'], c.collector_sha256())
        self.assertEqual(policy['concurrency'], 1)
        self.assertEqual(policy['retries'], 0)
        with self.assertRaises(shadow.ShadowError):
            c.create_collection_policy(adapter_sha256='a' * 64, runtime_policy_sha256='b' * 64,
                                       result_fields={'actions': 'array'})

    def test_prompt_only_and_input_commitments_are_checked_before_execution(self):
        args = fixture(SyntheticAdapter())
        raw = json.loads(args['inputs_bytes'])
        raw['inputs']['caseA'] += ' altered'
        with self.assertRaisesRegex(c.CollectionError, 'input_mismatch'):
            c._prepare(args['snapshot'], args['expected_candidate_id'], args['plan_bytes'],
                args['expected_plan_sha256'], args['suite_bytes'], args['policy_bytes'], encode(raw))
        candidate = args['snapshot'].candidate.to_dict()['candidate']
        candidate.pop('state')
        candidate.update(schema_version='hermes-learning-draft/1.0', kind='code_patch')
        changed = build_candidate(candidate)
        snapshot = CandidateSnapshot(changed, args['snapshot'].baseline, args['snapshot'].evidence)
        with self.assertRaisesRegex(c.CollectionError, 'prompt_only'):
            c._prepare(snapshot, changed.candidate_id, args['plan_bytes'], args['expected_plan_sha256'],
                        args['suite_bytes'], args['policy_bytes'], args['inputs_bytes'])

    def test_native_windows_refuses_before_creating_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'never-created'
            with patch.object(c.os, 'name', 'nt'), self.assertRaisesRegex(c.CollectionError, 'platform_unsupported'):
                c._Journal(target)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
