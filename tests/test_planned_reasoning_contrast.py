"""Fresh synthetic plans; no historical cohort or live runtime is read."""
import base64
import copy
import json
import unittest
from unittest.mock import patch

from hermes_dohaa.learning import native_prompt as native, shadow
from tools import planned_reasoning_contrast as contrast
from tools import prospective_reasoning_budget as budget


def embedded(value):
    raw = shadow._canonical(value)
    return dict(document_base64=base64.b64encode(raw).decode(),
                document_sha256=shadow._hash(raw))


def envelope(raw):
    return dict(document_bytes=raw, document_sha256=shadow._hash(raw))


def fixture(record_counts=(1, 4), *, two_requests=False):
    policy = dict(schema_version='hermes-native-tool-policy/1.0',
                  response_contract='controlled-tool-proposal/1.0', context_length=8192,
                  native_commit='a'*40, bridge_sha256=native.native_bridge_sha256(),
                  model='synthetic-contrast-model', endpoint='http://127.0.0.1:1234/v1',
                  reasoning_effort='none', seed=17, temperature=0.0, top_p=1.0,
                  max_tokens=1024, request_timeout_seconds=30, worker_timeout_seconds=40,
                  request_limit=2, worker_uid=60000, worker_gid=60000, exclusive_backend=True)
    profiles = dict(schema_version='hermes-reasoning-profiles/1.0', profiles={
        'none': embedded(policy), 'medium': embedded(dict(policy, reasoning_effort='medium'))})
    rules = dict(schema_version='hermes-reasoning-rules/1.0', default_profile='none', rules=[
        dict(rule_id='synthetic-many-records', feature='source_records', at_least=3, profile='medium')])
    tasks = []
    for i, count in enumerate(record_counts):
        task = dict(schema_version='hermes-budget-task/1.0', task_id=f'contrast-task-{i}',
                    public_input_sha256=shadow._hash(f'synthetic-input-{i}'.encode()),
                    required_items=['answer'], operation_steps=[],
                    source_records=[dict(source_id=f'source-{j}', revision=0) for j in range(count)],
                    planned_request_ids=[f'planned-{i}-{j}' for j in range(2 if two_requests else 1)])
        tasks.append(embedded(task))
    args = dict(token_ceiling=100_000)
    for name, value in (('workload', dict(schema_version='hermes-budget-workload/1.0', tasks=tasks)),
                        ('profiles', profiles), ('rules', rules)):
        args[name+'_bytes'] = shadow._canonical(value)
        args[name+'_sha256'] = shadow._hash(args[name+'_bytes'])
    return args


def plans_for(args):
    return {s: envelope(budget.build_plan(strategy=s, **args)) for s in contrast.STRATEGIES}


def replace_document(args, name, value):
    raw = shadow._canonical(value)
    args.update({name+'_bytes': raw, name+'_sha256': shadow._hash(raw)})


class PlannedContrastTests(unittest.TestCase):
    def test_mixed_assignments_have_contrast_against_both_fixed_choices(self):
        args = fixture(); plans = plans_for(args)
        raw = contrast.diagnose_contrast(plans=plans, **args)
        report = json.loads(raw)
        self.assertEqual(raw, shadow._canonical(report))
        self.assertEqual(report['status'], 'contrast_present')
        self.assertEqual(report['identical_to'], [])
        self.assertEqual(report['task_count'], 2)
        for fixed in ('fixed-none', 'fixed-medium'):
            self.assertEqual(report['comparisons'][fixed], dict(
                changed_task_count=1, unchanged_task_count=1, assignment_identical=False))
        self.assertEqual(report['plan_sha256'], {s: e['document_sha256'] for s, e in plans.items()})
        for key in ('execution_authorized', 'source_admitted', 'effective_mode_attested',
                    'quality_claim_supported', 'savings_measured'):
            self.assertIs(report[key], False)

    def test_constant_assignments_report_absence_even_with_different_strategy_names(self):
        for counts, fixed in (((1, 2), 'fixed-none'), ((3, 5), 'fixed-medium')):
            args = fixture(counts)
            with self.subTest(fixed=fixed):
                report = json.loads(contrast.diagnose_contrast(plans=plans_for(args), **args))
                self.assertEqual(report['status'], 'contrast_absent')
                self.assertEqual(report['identical_to'], [fixed])
                self.assertTrue(report['comparisons'][fixed]['assignment_identical'])

    def test_different_matched_rules_and_equal_caps_do_not_create_false_contrast(self):
        args = fixture((1, 4))
        rules = json.loads(args['rules_bytes'])
        rules['rules'][0]['profile'] = 'none'
        replace_document(args, 'rules', rules)
        plans = plans_for(args)
        adaptive = json.loads(plans['adaptive']['document_bytes'])
        self.assertNotEqual(adaptive['entries'][0]['matched_rule'], adaptive['entries'][1]['matched_rule'])
        report = json.loads(contrast.diagnose_contrast(plans=plans, **args))
        self.assertEqual(report['identical_to'], ['fixed-none'])

    def test_envelopes_require_exactly_three_known_strategies_and_fields(self):
        args = fixture(); original = plans_for(args)
        for bad in (None, [], {}, dict(original, invented=original['adaptive']),
                    {k: v for k, v in original.items() if k != 'fixed-medium'}):
            with self.subTest(value=type(bad).__name__), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=bad, **args)
        for value in (None, [], {}, dict(original['adaptive'], trusted=True)):
            plans = dict(original, adaptive=value)
            with self.subTest(envelope=value), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=plans, **args)

    def test_rehashed_plan_tampering_does_not_replace_original_commitments(self):
        args = fixture(); original = plans_for(args)
        for change in ('profile', 'input', 'requests', 'cap', 'drop', 'order', 'source'):
            plan = json.loads(original['adaptive']['document_bytes'])
            if change == 'drop': plan['entries'].pop()
            elif change == 'order': plan['entries'].reverse()
            elif change == 'source': plan['source_sha256'] = 'f'*64
            else:
                key, value = {'profile': ('profile', 'medium'),
                              'input': ('public_input_sha256', 'c'*64),
                              'requests': ('planned_request_ids', ['extra']),
                              'cap': ('max_tokens', 2048)}[change]
                plan['entries'][0][key] = value
            plans = dict(original, adaptive=envelope(shadow._canonical(plan)))
            with self.subTest(change=change), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=plans, **args)

    def test_valid_plan_from_another_workload_is_not_a_paired_baseline(self):
        args = fixture(); plans = plans_for(args)
        for other in (fixture((1,)), fixture((1, 5)), fixture(two_requests=True)):
            changed = dict(plans, **{'fixed-medium': plans_for(other)['fixed-medium']})
            with self.subTest(other=other['workload_sha256']), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=changed, **args)

    def test_swapped_strategy_envelopes_reject_even_with_valid_hashes(self):
        args = fixture(); plans = plans_for(args)
        plans['adaptive'], plans['fixed-medium'] = plans['fixed-medium'], plans['adaptive']
        with self.assertRaises(contrast.ContrastError):
            contrast.diagnose_contrast(plans=plans, **args)

    def test_changed_originals_or_nonfactor_settings_reject(self):
        args = fixture(); plans = plans_for(args)
        for name in ('workload', 'rules', 'profiles'):
            changed = dict(args); changed[name+'_sha256'] = '0'*64
            with self.subTest(document=name), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=plans, **changed)
        for field, value in (('model', 'other-model'), ('seed', 18), ('context_length', 16384)):
            changed = dict(args); library = json.loads(args['profiles_bytes'])
            policy = json.loads(base64.b64decode(library['profiles']['medium']['document_base64']))
            policy[field] = value; library['profiles']['medium'] = embedded(policy)
            replace_document(changed, 'profiles', library)
            with self.subTest(field=field), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=plans, **changed)

    def test_malformed_or_unbounded_plan_diagnostics_do_not_echo_payloads(self):
        args = fixture(); plans = plans_for(args)
        for raw in (b'{"private-payload":', b'[]', b' '* (budget.MAX_DOCUMENT_BYTES + 1)):
            changed = dict(plans, adaptive=envelope(raw))
            with self.subTest(size=len(raw)), self.assertRaises(contrast.ContrastError) as caught:
                contrast.diagnose_contrast(plans=changed, **args)
            self.assertEqual(str(caught.exception), 'plan_or_original_inputs_invalid')
        changed = dict(plans, adaptive=dict(document_bytes='private-payload', document_sha256='0'*64))
        with self.assertRaises(contrast.ContrastError):
            contrast.diagnose_contrast(plans=changed, **args)

    def test_whole_workload_opportunities_are_not_added_across_counterfactual_plans(self):
        args = fixture(two_requests=True)
        report = json.loads(contrast.diagnose_contrast(plans=plans_for(args), **args))
        self.assertEqual(report['planned_requests_per_strategy'], 4)
        self.assertEqual(report['native_requests'], 0)
        self.assertEqual(report['task_count'], 2)

    def test_invalid_ceiling_cannot_silently_drop_the_expensive_fixed_strategy(self):
        args = fixture(); plans = plans_for(args)
        for ceiling in (True, -1, 1024):
            with self.subTest(ceiling=ceiling), self.assertRaises(contrast.ContrastError):
                contrast.diagnose_contrast(plans=plans, **dict(args, token_ceiling=ceiling))

    def test_diagnostic_is_deterministic_non_mutating_and_has_no_runtime_actions(self):
        args = fixture(); plans = plans_for(args); before = copy.deepcopy((args, plans))
        with (patch.object(native.NativePromptAdapter, 'start', side_effect=AssertionError('load')),
              patch.object(native.NativePromptAdapter, '_http', side_effect=AssertionError('network')),
              patch.object(native.subprocess, 'Popen', side_effect=AssertionError('process'))):
            report = contrast.diagnose_contrast(plans=plans, **args)
            self.assertEqual(contrast.diagnose_contrast(plans=plans, **args), report)
        self.assertEqual((args, plans), before)

    def test_mixed_planner_source_measurements_reject_before_comparison(self):
        args = fixture()
        # Simulate a changing checkout measurement, not a real source attestation.
        measurements = dict(zip(contrast.STRATEGIES, ['a'*64, 'b'*64, 'c'*64]))
        plans = {}
        for strategy, digest in measurements.items():
            with patch.object(budget, 'source_sha256', return_value=digest):
                plans[strategy] = envelope(budget.build_plan(strategy=strategy, **args))
        validate = budget.validate_plan

        def validate_with_measured_source(raw, expected, *, strategy, **originals):
            with patch.object(budget, 'source_sha256', return_value=measurements[strategy]):
                return validate(raw, expected, strategy=strategy, **originals)

        with patch.object(budget, 'validate_plan', side_effect=validate_with_measured_source):
            with self.assertRaisesRegex(contrast.ContrastError, 'planner_source_changed'):
                contrast.diagnose_contrast(plans=plans, **args)


if __name__ == '__main__':
    unittest.main()
