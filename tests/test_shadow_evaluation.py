import copy
import hashlib
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

from hermes_dohaa.learning.artifacts import ArtifactBytes, CandidateSnapshot
from hermes_dohaa.learning.quarantine import build_candidate
from hermes_dohaa.learning.shadow import (
    ShadowError, _json, _publish, create_plan, evaluate_shadow, evaluator_sha256, main,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def completed(result, actions=None):
    return {'status': 'completed', 'result': result, 'actions': [] if actions is None else actions}


def fixture():
    baseline, evidence = b'private baseline prompt', b'private training evidence'
    candidate = build_candidate({
        'schema_version': 'hermes-learning-draft/1.0', 'kind': 'prompt',
        'baseline_sha256': digest(baseline), 'artifact': 'private proposed prompt',
        'rationale': 'synthetic fixture', 'evidence_sha256': [digest(evidence)],
    })
    snapshot = CandidateSnapshot(candidate, ArtifactBytes(digest(baseline), baseline),
                                 (ArtifactBytes(digest(evidence), evidence),))
    suite = {'schema_version': 'hermes-shadow-suite/1.0', 'cases': [
        {'case_id': 'first', 'input_sha256': digest(b'first input'), 'expected_result': {'answer': 42}},
        {'case_id': 'second', 'input_sha256': digest(b'second input'), 'expected_result': [True, 3]},
    ]}
    plan = create_plan(snapshot, expected_candidate_id=candidate.candidate_id,
        suite_bytes=encoded(suite), expected_suite_sha256=digest(encoded(suite)),
        execution_policy_sha256=digest(b'fixed synthetic execution policy'),
        min_improvements=1, min_candidate_correct=2)
    observations = {'schema_version': 'hermes-shadow-observations/1.0',
        'plan_sha256': digest(encoded(plan)),
        **{key: plan[key] for key in ('candidate_id', 'baseline_sha256', 'suite_sha256', 'execution_policy_sha256')},
        'trials': [
            {'case_id': case['case_id'], 'input_sha256': case['input_sha256'],
             'baseline': completed(None if index == 0 else case['expected_result']),
             'candidate': completed(case['expected_result'])}
            for index, case in enumerate(suite['cases'])],
    }
    return snapshot, suite, plan, observations


def score(f, **overrides):
    snapshot, suite, plan, observations = f
    arguments = dict(expected_candidate_id=snapshot.candidate.candidate_id,
        plan_bytes=encoded(plan), expected_plan_sha256=digest(encoded(plan)),
        suite_bytes=encoded(suite), observations_bytes=encoded(observations),
        expected_observations_sha256=digest(encoded(observations)))
    arguments.update(overrides)
    return evaluate_shadow(snapshot, **arguments)


class ShadowScoringTests(unittest.TestCase):
    def test_positive_paired_result_is_deterministic_and_grants_no_authority(self):
        f = fixture()
        result = score(f)
        self.assertEqual(result, score(f))
        self.assertEqual(result['verdict'], 'meets_predeclared_criteria')
        self.assertEqual(result['summary']['baseline']['correct'], 1)
        self.assertEqual(result['summary']['candidate']['correct'], 2)
        self.assertEqual(result['paired'], {'improvements': 1, 'regressions': 0, 'both_correct': 1, 'both_incorrect': 0})
        self.assertTrue(all(result['criteria_checks'].values()))
        self.assertEqual(result['candidate_state'], 'quarantined')
        self.assertFalse(result['activation_authorized'])
        self.assertFalse(result['execution_attested'])
        self.assertEqual(len(result['result_id']), 64)
        rendered = json.dumps(result)
        for private in ('private', 'first', 'second', 'answer', 'expected_result'):
            self.assertNotIn(private, rendered)

    def test_tie_is_not_improvement(self):
        f = fixture()
        f[3]['trials'][0]['baseline'] = copy.deepcopy(f[3]['trials'][0]['candidate'])
        result = score(f)
        self.assertEqual(result['verdict'], 'does_not_meet_predeclared_criteria')
        self.assertFalse(result['criteria_checks']['min_improvements'])
        self.assertEqual(result['paired']['both_correct'], 2)

    def test_one_improvement_cannot_hide_one_paired_regression(self):
        f = fixture()
        f[3]['trials'][1]['candidate']['result'] = None
        result = score(f)
        self.assertEqual(result['paired']['improvements'], 1)
        self.assertEqual(result['paired']['regressions'], 1)
        self.assertFalse(result['criteria_checks']['max_regressions'])
        self.assertEqual(result['verdict'], 'does_not_meet_predeclared_criteria')

    def test_candidate_failure_is_counted_and_preserved_for_every_allowed_code(self):
        for code in ('timeout', 'budget_exhausted', 'invalid_response', 'runtime_error', 'cancelled'):
            with self.subTest(code=code):
                f = fixture()
                f[3]['trials'][1]['candidate'] = {'status': 'failed', 'error_code': code}
                result = score(f)
                self.assertEqual(result['summary']['candidate']['failures'], 1)
                self.assertEqual(result['records'][1]['candidate']['error_code'], code)
                self.assertFalse(result['criteria_checks']['max_candidate_failures'])
                self.assertEqual(result['verdict'], 'does_not_meet_predeclared_criteria')

    def test_baseline_failure_is_not_dropped(self):
        f = fixture()
        f[3]['trials'][0]['baseline'] = {'status': 'failed', 'error_code': 'timeout'}
        result = score(f)
        self.assertEqual(result['summary']['baseline']['failures'], 1)
        self.assertEqual(result['case_count'], 2)
        self.assertEqual(result['records'][0]['baseline']['error_code'], 'timeout')

    def test_proposed_actions_disqualify_otherwise_correct_candidate_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'must-not-exist'
            action = f"__import__('pathlib').Path({str(sentinel)!r}).touch()"
            f = fixture()
            f[3]['trials'][0]['candidate']['actions'] = [action]
            result = score(f)
            self.assertEqual(result['summary']['candidate']['proposed_actions'], 1)
            self.assertFalse(result['criteria_checks']['max_candidate_actions'])
            self.assertEqual(result['summary']['candidate']['correct'], 1)
            self.assertFalse(sentinel.exists())
            self.assertNotIn(action, json.dumps(result))

    def test_strict_json_scoring_distinguishes_boolean_integer_float_and_array_order(self):
        for result in ({'answer': True}, {'answer': 42.0}, {'answer': 42, 'extra': 0}):
            with self.subTest(result=result):
                f = fixture()
                f[3]['trials'][0]['candidate']['result'] = result
                self.assertFalse(score(f)['records'][0]['candidate']['correct'])
        for result in ([3, True], [1, 3], [True, 3.0]):
            f = fixture()
            f[3]['trials'][1]['candidate']['result'] = result
            self.assertFalse(score(f)['records'][1]['candidate']['correct'])

    def test_record_order_can_change_but_pairing_uses_case_identity(self):
        f = fixture()
        expected = score(f)
        f[3]['trials'].reverse()
        result = score(f)
        self.assertEqual(result['records'], expected['records'])
        self.assertEqual(result['summary'], expected['summary'])
        self.assertNotEqual(result['observations_sha256'], expected['observations_sha256'])

    def test_missing_duplicate_extra_and_foreign_cases_cannot_produce_a_verdict(self):
        for change in ('missing', 'extra', 'duplicate', 'foreign'):
            with self.subTest(change=change):
                f = fixture()
                trials = f[3]['trials']
                if change == 'missing':
                    trials.pop()
                elif change == 'extra':
                    trials.append(copy.deepcopy(trials[0]))
                elif change == 'duplicate':
                    trials[1]['case_id'] = trials[0]['case_id']
                else:
                    trials[1]['case_id'] = 'foreign'
                with self.assertRaises(ShadowError):
                    score(f)

    def test_recording_bindings_and_per_case_input_must_match_the_plan(self):
        for field in ('candidate_id', 'baseline_sha256', 'suite_sha256', 'plan_sha256', 'execution_policy_sha256'):
            with self.subTest(field=field):
                f = fixture()
                f[3][field] = '0' * 64
                with self.assertRaises(ShadowError) as raised:
                    score(f)
                self.assertEqual(raised.exception.code, 'shadow.recording_mismatch')
        f = fixture()
        f[3]['trials'][0]['input_sha256'] = '0' * 64
        with self.assertRaises(ShadowError) as raised:
            score(f)
        self.assertEqual(raised.exception.code, 'shadow.input_mismatch')

    def test_changing_any_externally_pinned_document_fails_closed(self):
        f = fixture()
        for field in ('expected_plan_sha256', 'expected_observations_sha256'):
            with self.subTest(field=field), self.assertRaises(ShadowError):
                score(f, **{field: '0' * 64})
        f[1]['cases'][0]['expected_result'] = None
        with self.assertRaises(ShadowError):
            score(f)

    def test_relaxed_fixed_safety_criteria_wrong_evaluator_or_baseline_cannot_pass(self):
        for field in ('max_regressions', 'max_candidate_failures', 'max_candidate_actions'):
            for value in (1, False):
                f = fixture()
                f[2]['criteria'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ShadowError):
                    score(f)
        for field, value in (('evaluator_sha256', '0' * 64), ('evaluator', 'candidate-supplied'),
                             ('baseline_sha256', '0' * 64), ('case_count', True),
                             ('schema_version', 'future/9')):
            f = fixture()
            f[2][field] = value
            with self.subTest(field=field), self.assertRaises(ShadowError):
                score(f)

    def test_unknown_authority_fields_and_malformed_outcomes_are_rejected(self):
        for location in ('plan', 'criteria', 'recording', 'trial', 'outcome'):
            f = fixture()
            targets = {'plan': f[2], 'criteria': f[2]['criteria'], 'recording': f[3],
                       'trial': f[3]['trials'][0], 'outcome': f[3]['trials'][0]['candidate']}
            targets[location]['approved'] = True
            with self.subTest(location=location), self.assertRaises(ShadowError):
                score(f)
        for outcome in ({'status': 'skipped'}, {'status': 'failed', 'error_code': 'private path'},
                        {'status': 'failed', 'error_code': 'timeout', 'result': 42},
                        completed(42, 'actions'), completed(42, [1]), None):
            f = fixture()
            f[3]['trials'][0]['candidate'] = outcome
            with self.assertRaises(ShadowError):
                score(f)

    def test_public_snapshot_instances_cannot_forge_verified_bytes(self):
        f = fixture()
        original = f[0]
        variants = (
            CandidateSnapshot(original.candidate, ArtifactBytes(original.baseline.sha256, b'changed'), original.evidence),
            CandidateSnapshot(original.candidate, original.baseline, ()),
            CandidateSnapshot(original.candidate, original.baseline,
                              (ArtifactBytes('0' * 64, original.evidence[0].content),)),
        )
        for variant in variants:
            with self.assertRaises(ShadowError):
                score((variant, *f[1:]))
        with self.assertRaises(ShadowError):
            score(f, expected_candidate_id='0' * 64)

    def test_scoring_does_not_mutate_inputs_or_reuse_a_mutated_report(self):
        f = fixture()
        before = copy.deepcopy(f[1:])
        result = score(f)
        result['criteria']['min_improvements'] = 0
        result['records'][0]['candidate']['correct'] = False
        self.assertEqual(f[1:], before)
        self.assertEqual(score(f)['criteria']['min_improvements'], 1)


class ShadowParsingAndPlanTests(unittest.TestCase):
    def test_strict_parser_rejects_duplicate_keys_nonfinite_unicode_and_deep_values(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e9999}',
                    b'{"a":"\\ud800"}', b'{"a":' + b'[' * 40 + b'0' + b']' * 40 + b'}', b'\xff', b'[]'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ShadowError):
                _json(raw, digest(raw))

    def test_json_byte_and_node_limits_are_enforced(self):
        with patch('hermes_dohaa.learning.shadow.MAX_JSON_BYTES', 2):
            with self.assertRaises(ShadowError) as raised:
                _json(b'{"a":1}', digest(b'{"a":1}'))
            self.assertEqual(raised.exception.code, 'shadow.document_limit')
        with patch('hermes_dohaa.learning.shadow.MAX_NODES', 2):
            with self.assertRaises(ShadowError) as raised:
                _json(b'{"a":[1,2]}', digest(b'{"a":[1,2]}'))
            self.assertEqual(raised.exception.code, 'shadow.structure_limit')

    def test_plan_rejects_duplicate_inputs_cases_and_invalid_minimums(self):
        f = fixture()
        for changes in ('duplicate_id', 'duplicate_input', 'one_case', 'extra_key'):
            suite = copy.deepcopy(f[1])
            if changes == 'duplicate_id':
                suite['cases'][1]['case_id'] = suite['cases'][0]['case_id']
            elif changes == 'duplicate_input':
                suite['cases'][1]['input_sha256'] = suite['cases'][0]['input_sha256']
            elif changes == 'one_case':
                suite['cases'].pop()
            else:
                suite['cases'][0]['evaluator'] = 'candidate-defined'
            with self.subTest(changes=changes), self.assertRaises(ShadowError):
                create_plan(f[0], expected_candidate_id=f[0].candidate.candidate_id,
                    suite_bytes=encoded(suite), expected_suite_sha256=digest(encoded(suite)),
                    execution_policy_sha256='a' * 64, min_improvements=1, min_candidate_correct=1)
        for minimum in (True, 0, -1, 3, 1.0):
            with self.subTest(minimum=minimum), self.assertRaises(ShadowError):
                create_plan(f[0], expected_candidate_id=f[0].candidate.candidate_id,
                    suite_bytes=encoded(f[1]), expected_suite_sha256=digest(encoded(f[1])),
                    execution_policy_sha256='a' * 64, min_improvements=minimum, min_candidate_correct=2)

    def test_unavailable_evaluator_sources_fail_closed_without_raw_error(self):
        with patch('pathlib.Path.read_bytes', side_effect=OSError('private source path')):
            with self.assertRaises(ShadowError) as raised:
                evaluator_sha256()
        self.assertEqual(str(raised.exception), 'shadow.evaluator_unavailable')

    def test_cli_failure_does_not_disclose_raw_os_error(self):
        output = io.StringIO()
        with patch('hermes_dohaa.learning.shadow.load_artifact_snapshot', side_effect=OSError('private content')), redirect_stdout(output):
            code = main(['plan', '--candidate', 'unused', '--candidate-id', 'a' * 64,
                         '--artifact-dir', 'unused', '--suite', 'unused', '--suite-sha256', 'b' * 64,
                         '--execution-policy-sha256', 'c' * 64, '--min-improvements', '1',
                         '--min-candidate-correct', '2', '--output', 'unused'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'shadow.io_error')
        self.assertNotIn('private', output.getvalue())


@unittest.skipUnless(os.name == 'posix', 'private publication and loading require POSIX')
class ShadowPublicationTests(unittest.TestCase):
    def test_concurrent_publication_has_exactly_one_winner_and_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'result.json'
            def publish(value):
                try:
                    _publish(target, value)
                    return True
                except ShadowError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(publish, (b'first complete report', b'second complete report')))
            self.assertEqual(sum(results), 1)
            self.assertIn(target.read_bytes(), (b'first complete report', b'second complete report'))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(list(root.iterdir()), [target])

    def test_symlink_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / 'existing'
            existing.write_bytes(b'original')
            target = root / 'link'
            target.symlink_to(existing)
            with self.assertRaises(ShadowError):
                _publish(target, b'new')
            self.assertEqual(existing.read_bytes(), b'original')

    def test_cli_pins_plan_before_recording_and_retains_a_negative_result(self):
        f = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path, suite_path = root / 'candidate.json', root / 'suite.json'
            candidate_path.write_bytes(encoded(f[0].candidate.to_dict()))
            suite_path.write_bytes(encoded(f[1]))
            for item in (f[0].baseline, *f[0].evidence):
                (root / item.sha256).write_bytes(item.content)
            plan_path, observations_path, result_path = root / 'plan.json', root / 'observations.json', root / 'result.json'
            shared = ['--candidate', str(candidate_path), '--candidate-id', f[0].candidate.candidate_id,
                      '--artifact-dir', str(root), '--suite', str(suite_path)]
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['plan', *shared, '--suite-sha256', digest(suite_path.read_bytes()),
                    '--execution-policy-sha256', f[2]['execution_policy_sha256'], '--min-improvements', '1',
                    '--min-candidate-correct', '2', '--output', str(plan_path)])
            self.assertEqual(code, 0)
            pinned_plan = json.loads(output.getvalue())['sha256']
            self.assertFalse(observations_path.exists())
            self.assertEqual(digest(plan_path.read_bytes()), pinned_plan)
            observations = f[3]
            observations['plan_sha256'] = pinned_plan
            observations['trials'][0]['baseline'] = copy.deepcopy(observations['trials'][0]['candidate'])
            observations_path.write_bytes(encoded(observations))
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['score', *shared, '--plan', str(plan_path), '--plan-sha256', pinned_plan,
                    '--observations', str(observations_path), '--observations-sha256', digest(observations_path.read_bytes()),
                    '--output', str(result_path)])
            self.assertEqual(code, 2)
            result = json.loads(result_path.read_bytes())
            self.assertEqual(result['verdict'], 'does_not_meet_predeclared_criteria')
            self.assertFalse(result['activation_authorized'])
            self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
            self.assertNotIn('private', output.getvalue())
