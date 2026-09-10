"""Fresh public development controls, not model measurements or holdouts."""
import copy
import hashlib
import unittest

from tools.document_stream import DocumentEvent as E
from tools.document_stream_request import canonical, make_request
from tools import query_memory as memory


def request(events=(), keys=('alpha', 'beta'), tick=8):
    return make_request(dict(run_id='memory-dev', at_tick=tick, events=[e.__dict__ for e in events]),
                        dict(task_id='memory', instruction='Report current requested facts with evidence.', fact_keys=list(keys)))


def with_events(raw, events):
    return make_request(dict(raw['observation'], events=events), raw['task'])


def selected(view):
    return [e['source_id'] for e in view['observation']['events']]


class QueryMemoryTests(unittest.TestCase):
    def select(self, raw, strategy='coverage_per_byte', budget=65536):
        return memory.select_memory(raw, strategy=strategy, max_bytes=budget)

    def test_coverage_keeps_combined_support_that_round_robin_can_miss(self):
        raw = request([E('e1', 'combined', 1, 1, 'alpha=11 beta=22'),
                       E('e2', 'alpha-only', 1, 2, 'alpha=11 '+('details '*18)),
                       E('e3', 'beta-only', 1, 3, 'beta=22 '+('details '*18))])
        budget = len(canonical(with_events(raw, [raw['observation']['events'][1]])))
        coverage, _ = self.select(raw, budget=budget)
        simple, _ = self.select(raw, 'per_key_recency', budget)
        self.assertEqual(selected(coverage), ['combined'])
        self.assertEqual(selected(simple), ['alpha-only'])

    def test_keyword_stuffing_counterexample_is_not_mistaken_for_truth(self):
        raw = request([E('e1', 'bait', 1, 1, 'alpha beta'),
                       E('e2', 'authority', 1, 2, 'alpha=11 beta=22 '+('context '*25))])
        budget = len(canonical(with_events(raw, [raw['observation']['events'][1]])))
        coverage, receipt = self.select(raw, budget=budget)
        simple, _ = self.select(raw, 'per_key_recency', budget)
        self.assertEqual(selected(coverage), ['bait'])
        self.assertEqual(selected(simple), ['authority'])
        self.assertEqual(receipt['lexically_covered_keys'], ['alpha', 'beta'])
        self.assertFalse(receipt['semantic_sufficiency_verified'])

    def test_if_all_active_documents_fit_both_methods_keep_them(self):
        raw = request([E('e1', 'unrelated', 1, 1, 'background'), E('e2', 'facts', 1, 2, 'alpha beta')])
        for strategy in memory.STRATEGIES:
            view, receipt = self.select(raw, strategy, len(canonical(raw)))
            self.assertEqual(view, raw)
            self.assertEqual(receipt['output_bytes'], len(canonical(raw)))

    def test_exact_utf8_boundary_includes_header_metadata_and_escaping(self):
        raw = request([E('e1', 'facts', 1, 1, 'alpha="sí" beta=☀\n')])
        size = len(canonical(raw))
        for strategy in memory.STRATEGIES:
            view, receipt = self.select(raw, strategy, size)
            self.assertEqual(view, raw); self.assertEqual(receipt['output_bytes'], size)
            view, _ = self.select(raw, strategy, size-1)
            self.assertEqual(selected(view), [])

    def test_array_separator_counts_for_multiple_documents(self):
        raw = request([E('e1', 'a', 1, 1, 'alpha'), E('e2', 'b', 1, 2, 'beta')])
        for strategy in memory.STRATEGIES:
            self.assertEqual(self.select(raw, strategy, len(canonical(raw)))[0], raw)
            self.assertEqual(len(selected(self.select(raw, strategy, len(canonical(raw))-1)[0])), 1)

    def test_oversized_source_is_skipped_without_truncation(self):
        raw = request([E('e1', 'small', 1, 1, 'alpha beta'),
                       E('e2', 'large', 1, 2, 'alpha beta '+('z'*500))])
        budget = len(canonical(with_events(raw, [raw['observation']['events'][0]])))
        for strategy in memory.STRATEGIES:
            view, _ = self.select(raw, strategy, budget)
            self.assertEqual(view['observation']['events'], [raw['observation']['events'][0]])

    def test_task_alone_must_fit_and_invalid_limits_or_strategies_fail(self):
        raw = request()
        for budget in (True, 0, -1, 65537, len(canonical(raw))-1):
            with self.assertRaises(ValueError): self.select(raw, budget=budget)
        with self.assertRaises(ValueError): self.select(raw, strategy='oracle')

    def test_empty_snapshot_and_exact_empty_header(self):
        raw = request()
        for strategy in memory.STRATEGIES:
            view, receipt = self.select(raw, strategy, len(canonical(raw)))
            self.assertEqual(view, raw); self.assertEqual(receipt['selected_events'], 0)

    def test_unmatched_sources_use_shared_recency_fill(self):
        raw = request([E('e1', 'old', 1, 1, 'context'), E('e2', 'new', 1, 2, 'context')])
        budget = len(canonical(with_events(raw, [raw['observation']['events'][1]])))
        for strategy in memory.STRATEGIES:
            view, receipt = self.select(raw, strategy, budget)
            self.assertEqual(selected(view), ['new']); self.assertEqual(receipt['lexically_covered_keys'], [])

    def test_unicode_normalization_and_all_key_tokens_are_required(self):
        raw = request([E('e1', 'unicode', 1, 1, 'ＡＬＰＨＡ total'),
                       E('e2', 'partial', 1, 2, 'alpha only')], keys=('alpha_total',))
        budget = len(canonical(with_events(raw, [raw['observation']['events'][0]])))
        for strategy in memory.STRATEGIES:
            view, receipt = self.select(raw, strategy, budget)
            self.assertEqual(selected(view), ['unicode'])
            self.assertEqual(receipt['lexically_covered_keys'], ['alpha_total'])

    def test_final_order_is_delivery_order_even_if_selection_order_differs(self):
        raw = request([E('e1', 'beta-source', 1, 1, 'beta'), E('e2', 'alpha-source', 1, 2, 'alpha')])
        for strategy in memory.STRATEGIES:
            self.assertEqual(self.select(raw, strategy)[0], raw)

    def test_ties_are_deterministic_without_changing_source_text(self):
        raw = request([E('e1', 'bbb', 1, 1, 'alpha beta'), E('e2', 'aaa', 1, 1, 'alpha beta')])
        budget = len(canonical(with_events(raw, [raw['observation']['events'][0]])))
        for strategy in memory.STRATEGIES:
            first = self.select(raw, strategy, budget)
            self.assertEqual(first, self.select(raw, strategy, budget))
            self.assertEqual(selected(first[0]), ['aaa'])

    def test_retraction_expiry_and_late_old_revision_never_revive_a_source(self):
        raw = request([E('e1', 'gone', 2, 1, '', retracted=True), E('e2', 'gone', 1, 2, 'alpha beta'),
                       E('e3', 'expired', 1, 3, 'alpha'), E('e4', 'expired', 2, 4, 'beta', expires_at=8)])
        for strategy in memory.STRATEGIES:
            self.assertEqual(selected(self.select(raw, strategy)[0]), [])

    def test_recompute_from_full_history_at_next_tick_preserves_tombstone(self):
        events = [E('e1', 'a', 1, 1, 'alpha beta'), E('e2', 'a', 2, 2, '', retracted=True)]
        for strategy in memory.STRATEGIES:
            self.assertEqual(selected(self.select(request(events[:1], tick=1), strategy)[0]), ['a'])
            self.assertEqual(selected(self.select(request(events, tick=2), strategy)[0]), [])

    def test_invalid_or_future_deliveries_fail_before_being_filtered(self):
        raw = request([E('e1', 'irrelevant', 1, 1, 'unmatched')])
        for event in (E('e2', 'irrelevant', 1, 2, 'conflict'), E('e2', 'other', 1, 9, 'future')):
            changed = copy.deepcopy(raw); changed['observation']['events'].append(event.__dict__)
            for strategy in memory.STRATEGIES:
                with self.assertRaises(ValueError): self.select(changed, strategy)

    def test_no_mutation_or_alias_and_instructions_are_unchanged(self):
        raw = request([E('e1', 'a', 1, 1, 'alpha beta')]); before = copy.deepcopy(raw)
        view, receipt = self.select(raw)
        self.assertEqual(raw, before); self.assertEqual(view['task'], before['task'])
        view['task']['instruction'] = 'changed'; view['observation']['events'][0]['text'] = 'changed'
        self.assertEqual(raw, before)

    def test_receipt_binds_full_input_output_budget_strategy_and_source(self):
        raw = request([E('e1', 'a', 1, 1, 'old'), E('e2', 'a', 2, 2, 'alpha beta')])
        view, receipt = self.select(raw)
        self.assertTrue(memory.verify_selection(raw, view, receipt, strategy='coverage_per_byte', max_bytes=65536))
        changed = copy.deepcopy(raw); changed['observation']['events'][0]['text'] = 'changed removed content'
        self.assertFalse(memory.verify_selection(changed, view, receipt, strategy='coverage_per_byte', max_bytes=65536))
        for key, value in (('selected_events', True), ('selector_sha256', '0'*64), ('budget_bytes', 1024), ('extra', 'bad')):
            bad = dict(receipt); bad[key] = value
            self.assertFalse(memory.verify_selection(raw, view, bad, strategy='coverage_per_byte', max_bytes=65536))
        self.assertFalse(memory.verify_selection(raw, view, receipt, strategy='per_key_recency', max_bytes=65536))
        self.assertEqual(receipt['input_sha256'], hashlib.sha256(canonical(raw)).hexdigest())
        self.assertEqual(receipt['output_sha256'], hashlib.sha256(canonical(view)).hexdigest())

    def test_source_ids_are_not_query_evidence(self):
        raw = request([E('e1', 'alpha-beta', 1, 1, 'no matching content')])
        self.assertEqual(self.select(raw)[1]['lexically_covered_keys'], [])
