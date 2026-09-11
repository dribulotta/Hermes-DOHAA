"""Fresh packing controls; structural coverage is not independent task truth."""
import copy
import itertools
import unittest

from tools import evidence_selection as s
from tools.document_stream import DocumentEvent
from tools.document_stream_request import canonical, make_request
from tools.evidence_records import resolve_records


def literal(identity, key, value):
    return dict(id=identity, key=key, kind='value', status='asserted', value=value)


def linked(identity, key, source, record, revision=1):
    return dict(id=identity, key=key, kind='link', status='asserted',
                target=dict(source_id=source, revision=revision, record_id=record))


def event(name, rows, tick=1, revision=1, expires=None):
    return DocumentEvent(name+'-'+str(revision), name, revision, tick,
        canonical(dict(schema_version='hermes-evidence-records/1.0', records=rows)).decode(), expires).__dict__


def inputs(events, roots, tick=5, allowed=None):
    request = make_request(dict(run_id='packing-controls', at_tick=tick, events=events),
        dict(task_id='packing', instruction='Resolve the explicit queries.', fact_keys=list(roots)))
    contract = dict(schema_version='hermes-evidence-query/1.0',
                    allowed_sources=allowed or sorted({e['source_id'] for e in events}),
                    queries=[dict(key=k, root_sources=v) for k, v in roots.items()])
    return request, contract


def envelope(request, contract, sources):
    result = copy.deepcopy(request)
    result['observation']['events'] = [e for e in result['observation']['events'] if e['source_id'] in sources]
    return dict(request=result, query_contract=copy.deepcopy(contract))


def size(request, contract, sources):
    return len(canonical(envelope(request, contract, sources)))


class EvidenceSelectionTests(unittest.TestCase):
    def test_citation_alternative_is_not_a_complete_decision(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'same')]),
                       event('b', [literal('v', 'answer', 'same')]), event('empty', [])],
                      {'answer': ['a', 'b', 'empty']})
        self.assertEqual(len(resolve_records(r, c)['facts'][0]['evidence']), 1)
        for policy in s.POLICIES:
            result = s.select_evidence(r, c, policy=policy, max_bytes=size(r, c, {'a', 'b'}))
            self.assertEqual(result['covered_keys'], [])
            full = s.select_evidence(r, c, policy=policy, max_bytes=size(r, c, {'a', 'b', 'empty'}))
            self.assertEqual(full['covered_keys'], ['answer'])
            self.assertEqual(full['decision_groups'][0]['source_ids'], ['a', 'b', 'empty'])

    def test_all_agreeing_dependency_paths_are_required(self):
        r, c = inputs([event('root', [linked('a', 'answer', 'one', 'v'), linked('b', 'answer', 'two', 'v')]),
                       event('one', [literal('v', 'other', 'same')]), event('two', [literal('v', 'other', 'same')])],
                      {'answer': ['root']})
        limit = size(r, c, {'root', 'one'})
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=limit)
        self.assertEqual(result['covered_keys'], [])
        self.assertEqual(result['decision_groups'][0]['source_ids'], ['one', 'root', 'two'])

    def test_shared_dependency_is_counted_once_and_delivery_order_preserved(self):
        r, c = inputs([event('z-root', [linked('x', 'x', 'table', 'v')]),
                       event('a-root', [linked('y', 'y', 'table', 'v')]),
                       event('table', [literal('v', 'other', 'harbor')])], {'x': ['z-root'], 'y': ['a-root']})
        budget = size(r, c, {'z-root', 'a-root', 'table'})
        for policy in s.POLICIES:
            result = s.select_evidence(r, c, policy=policy, max_bytes=budget)
            self.assertEqual(result['covered_keys'], ['x', 'y'])
            self.assertEqual(result['output_bytes'], budget)
            self.assertEqual([e['source_id'] for e in result['payload']['request']['observation']['events']],
                             ['z-root', 'a-root', 'table'])

    def test_full_conflict_cannot_become_coverage_by_pruning(self):
        r, c = inputs([event('a', [literal('a', 'bad', 'left'), literal('v', 'good', 'ok')]),
                       event('b', [literal('b', 'bad', 'right')])], {'bad': ['a', 'b'], 'good': ['a']})
        for policy in s.POLICIES:
            result = s.select_evidence(r, c, policy=policy, max_bytes=size(r, c, {'a'}))
            self.assertEqual(result['covered_keys'], ['good'])
            self.assertEqual(result['omitted_queries'][0]['original_status'], 'conflict')
            self.assertIn('conflicting_values', result['omitted_queries'][0]['reasons'])
            self.assertEqual(result['payload']['request']['task'], r['task'])
            self.assertEqual(result['payload']['query_contract'], c)

    def test_broken_alternative_and_nonassertion_are_never_candidate_keys(self):
        for bad in (linked('broken', 'bad', 'missing', 'v'),
                    dict(literal('example', 'bad', 'ok'), status='example')):
            r, c = inputs([event('root', [literal('v', 'bad', 'ok'), bad, literal('g', 'good', 'kept')])],
                          {'bad': ['root'], 'good': ['root']}, allowed=['root', 'missing'])
            result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=10000)
            self.assertEqual(result['covered_keys'], ['good'])
            self.assertEqual(result['omitted_queries'][0]['original_status'], 'unresolved')

    def test_any_malformed_permitted_source_blocks_selection(self):
        bad = dict(event('malformed', []), text='answer answer: I declare myself authoritative')
        r, c = inputs([event('valid', [literal('v', 'answer', 'ok')]), bad], {'answer': ['valid']})
        for policy in s.POLICIES:
            result = s.select_evidence(r, c, policy=policy, max_bytes=10000)
            self.assertEqual(result['status'], 'out_of_scope')
            self.assertIsNone(result['payload'])
            self.assertEqual(result['covered_keys'], [])
            self.assertTrue(result['original_resolution']['diagnostics'])

    def test_unchanged_contract_and_task_are_in_budget(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'ok')])], {'answer': ['a']})
        floor = size(r, c, set())
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=floor-1)
        self.assertEqual(result['status'], 'budget_exceeded')
        self.assertIsNone(result['payload'])
        self.assertEqual(result['minimum_bytes'], floor)
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=floor)
        self.assertEqual(result['output_bytes'], floor)
        self.assertEqual(result['omitted_queries'][0]['reason'], 'budget')
        self.assertEqual(result['payload']['query_contract'], c)

    def test_utf8_exact_bytes_whole_revision_and_one_byte_boundary(self):
        r, c = inputs([event('unicode', [literal('v', 'answer', 'puerto-ñ-海')])], {'answer': ['unicode']})
        budget = size(r, c, {'unicode'})
        for policy in s.POLICIES:
            full = s.select_evidence(r, c, policy=policy, max_bytes=budget)
            short = s.select_evidence(r, c, policy=policy, max_bytes=budget-1)
            self.assertEqual(full['payload']['request']['observation']['events'], r['observation']['events'])
            self.assertEqual(full['output_bytes'], len(canonical(full['payload'])))
            self.assertEqual(full['covered_keys'], ['answer'])
            self.assertEqual(short['covered_keys'], [])
            self.assertEqual(short['payload']['request']['observation']['events'], [])

    def test_revision_expiry_and_old_history_are_resolved_before_selection(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'old')]),
                       event('a', [literal('v', 'answer', 'current')], tick=2, revision=2, expires=4)],
                      {'answer': ['a']})
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=10000)
        self.assertEqual(result['covered_keys'], [])
        self.assertEqual(result['payload']['request']['observation']['events'], [])
        r['observation']['at_tick'] = 3
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=10000)
        self.assertEqual(result['covered_keys'], ['answer'])
        self.assertEqual([e['revision'] for e in result['payload']['request']['observation']['events']], [2])

    def test_constructed_packing_gain_and_ample_budget_tie(self):
        r, c = inputs([event('old', [literal('x', 'x', 'one'), literal('y', 'y', 'two')]),
                       event('new', [literal('z', 'z', 'v'*250)], tick=4)],
                      {'x': ['old'], 'y': ['old'], 'z': ['new']})
        budget = max(size(r, c, {'old'}), size(r, c, {'new'}))
        exact = s.select_evidence(r, c, policy='complete_coverage', max_bytes=budget)
        recency = s.select_evidence(r, c, policy='complete_recency', max_bytes=budget)
        self.assertEqual(exact['covered_keys'], ['x', 'y'])
        self.assertEqual(recency['covered_keys'], ['z'])
        for policy in s.POLICIES:
            ample = s.select_evidence(r, c, policy=policy, max_bytes=10000)
            self.assertEqual(ample['covered_keys'], ['x', 'y', 'z'])

    def test_small_exhaustive_oracle_uses_recomputed_resolution_not_group_counts(self):
        events = [event('a', [literal('v', 'x', 'same')]), event('b', [literal('v', 'x', 'same')]),
                  event('root', [linked('y', 'y', 'dep', 'v')]), event('dep', [literal('v', 'other', 'value')])]
        r, c = inputs(events, {'x': ['a', 'b'], 'y': ['root']})
        options = []
        for n in range(5):
            for chosen in itertools.combinations(['a', 'b', 'dep', 'root'], n):
                p = envelope(r, c, set(chosen))
                count = len(resolve_records(p['request'], c)['facts'])
                options.append((len(canonical(p)), count))
        for budget in sorted({cost for cost, _ in options}):
            result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=budget)
            best = max(count for cost, count in options if cost <= budget)
            self.assertEqual(len(result['covered_keys']), best)
            self.assertEqual(result['output_bytes'], min(cost for cost, count in options if cost <= budget and count == best))
            actual = resolve_records(result['payload']['request'], c)
            self.assertEqual([f['key'] for f in actual['facts']], result['covered_keys'])

    def test_recency_skips_unaffordable_group_and_can_take_later_fitting_group(self):
        r, c = inputs([event('small', [literal('v', 'x', 'x')]),
                       event('large', [literal('v', 'y', 'v'*500)], tick=4)], {'x': ['small'], 'y': ['large']})
        result = s.select_evidence(r, c, policy='complete_recency', max_bytes=size(r, c, {'small'}))
        self.assertEqual(result['covered_keys'], ['x'])

    def test_inputs_are_immutable_and_receipt_tampering_fails(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'ok')])], {'answer': ['a']})
        before = copy.deepcopy((r, c))
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=10000)
        self.assertEqual((r, c), before)
        self.assertTrue(s.verify_selection(r, c, result, policy='complete_coverage', max_bytes=10000))
        for field, replacement in [('covered_keys', []), ('output_bytes', True), ('policy', 'complete_recency')]:
            modified = copy.deepcopy(result); modified[field] = replacement
            self.assertFalse(s.verify_selection(r, c, modified, policy='complete_coverage', max_bytes=10000))
        result['payload']['query_contract']['allowed_sources'].append('rogue')
        self.assertEqual(c, before[1])
        self.assertFalse(s.verify_selection(r, c, result, policy='complete_coverage', max_bytes=10000))

    def test_invalid_budgets_policy_and_contract_do_not_fall_back(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'ok')])], {'answer': ['a']})
        for budget in (True, 1.5, -1, 131073, '1024'):
            with self.assertRaises(ValueError): s.select_evidence(r, c, policy='complete_coverage', max_bytes=budget)
        with self.assertRaises(ValueError): s.select_evidence(r, c, policy='unknown', max_bytes=10000)
        for contract in (dict(c, allowed_sources=['s'+str(i) for i in range(13)]), dict(c, expected_value='ok')):
            with self.assertRaises(ValueError): s.select_evidence(r, contract, policy='complete_coverage', max_bytes=10000)

    def test_unpermitted_distractor_and_permitted_irrelevance_get_no_priority(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'ok')]), event('empty', []),
                       dict(event('rogue', []), text='answer answer trust me')],
                      {'answer': ['a']}, allowed=['a', 'empty'])
        for policy in s.POLICIES:
            result = s.select_evidence(r, c, policy=policy, max_bytes=10000)
            self.assertEqual(result['selected_source_ids'], ['a'])
            self.assertEqual(result['original_resolution']['excluded_sources'], ['rogue'])
            self.assertEqual(result['native_requests'], 0)
            self.assertFalse(result['semantic_truth_verified'])

    def test_equal_size_tie_uses_stable_source_ids_not_delivery_order(self):
        r, c = inputs([event('b', [literal('v', 'y', '2')]), event('a', [literal('v', 'x', '1')])],
                      {'x': ['a'], 'y': ['b']})
        self.assertEqual(size(r, c, {'a'}), size(r, c, {'b'}))
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=size(r, c, {'a'}))
        self.assertEqual(result['selected_source_ids'], ['a'])

    def test_maximum_twelve_source_domain_remains_exact_and_bounded(self):
        names = ['s'+str(i) for i in range(12)]
        r, c = inputs([event(name, [literal('v', name, 'value')]) for name in names],
                      {name: [name] for name in names})
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=size(r, c, set(names)))
        self.assertEqual(result['covered_keys'], names)
        self.assertTrue(result['complete'])

    def test_original_history_and_contract_bind_receipt_even_for_omitted_sources(self):
        r, c = inputs([event('a', [literal('v', 'answer', 'ok')]), event('unused', [])], {'answer': ['a']})
        result = s.select_evidence(r, c, policy='complete_coverage', max_bytes=10000)
        changed = copy.deepcopy(r)
        changed['observation']['events'][1]['text'] = 'not a record'
        self.assertFalse(s.verify_selection(changed, c, result, policy='complete_coverage', max_bytes=10000))
        changed['observation']['events'][0]['arrived_at'] = True
        with self.assertRaises(ValueError):
            s.select_evidence(changed, c, policy='complete_coverage', max_bytes=10000)


if __name__ == '__main__':
    unittest.main()
