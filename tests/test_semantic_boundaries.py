"""Synthetic failures at date and JSON Pointer boundaries; no model calls."""
import copy
import unittest

from hermes_dohaa.assurance.gates import SemanticAssertionsGate
from hermes_dohaa.assurance.semantic_assertions import SemanticEvaluationError, _resolve_pointer
from hermes_dohaa.controller.engine import DohaaController, RunStatus
from hermes_dohaa.controller.semantic_repair import (
    _replace_existing_pointer, propose_deterministic_semantic_repair,
)
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_semantic_assertions import assertion, expression, ref, semantic_contract


def temporal_contract(op, start, days, *, holidays=None):
    inputs = {'start': start, 'days': days}
    args = [ref('inputs', '/start'), ref('inputs', '/days')]
    if holidays is not None:
        inputs['holidays'] = holidays
        args.append(ref('inputs', '/holidays'))
    inputs['semantic_assertions'] = [assertion(
        'date-boundary', 'equals', ref('result', '/value'), expression(op, *args))]
    return semantic_contract(inputs, max_attempts=1)


class SyntheticRuntime:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def propose(self, contract, feedback):
        self.calls += 1
        return Proposal(result=self.value)


class SemanticBoundaryTests(unittest.TestCase):
    def test_date_overflow_is_a_value_free_gate_failure_in_both_directions(self):
        for op in ('add_days', 'add_business_days'):
            for start, days in (('9999-12-31', 1), ('0001-01-01', -1)):
                with self.subTest(op=op, start=start):
                    result = SemanticAssertionsGate().evaluate(
                        temporal_contract(op, start, days), Proposal(result={'value': 'unchanged'}))
                    self.assertFalse(result.passed)
                    violation = result.details['violations'][0]
                    self.assertEqual(violation['error_code'], 'temporal.date_out_of_range')
                    self.assertEqual(violation['operation'], op)
                    self.assertNotIn(start, str(result.to_dict()))

    def test_calendar_boundary_failure_records_a_terminal_without_extra_attempts(self):
        runtime = SyntheticRuntime({'value': 'unchanged'})
        with EvidenceLedger() as ledger:
            result = DohaaController(runtime, (SemanticAssertionsGate(),), ledger).run(
                temporal_contract('add_business_days', '9999-12-30', 1,
                                  holidays=['9999-12-31']))
            self.assertEqual(result.status, RunStatus.ESCALATED)
            self.assertEqual(runtime.calls, 1)
            self.assertEqual(sum(r.event_type == 'run.finished' for r in ledger.records()), 1)
            self.assertTrue(ledger.verify_chain())

    def test_unrepresentable_date_cannot_be_repaired_or_mutate_the_proposal(self):
        for op in ('add_days', 'add_business_days'):
            candidate = Proposal(result={'value': 'unchanged'})
            self.assertIsNone(propose_deterministic_semantic_repair(
                temporal_contract(op, '9999-12-31', 1), candidate))
            self.assertEqual(candidate.result, {'value': 'unchanged'})

    def test_representable_dates_keep_boundaries_weekends_holidays_and_zero(self):
        cases = [('add_days', '9999-12-30', 1, '9999-12-31'),
                 ('add_days', '0001-01-02', -1, '0001-01-01'),
                 ('add_days', '2024-02-28', 1, '2024-02-29'),
                 ('add_business_days', '9999-12-31', 0, '9999-12-31'),
                 ('add_business_days', '0001-01-01', 0, '0001-01-01'),
                 ('add_business_days', '2024-03-01', 1, '2024-03-04'),
                 ('add_business_days', '2024-03-04', -1, '2024-03-01')]
        for op, start, days, expected in cases:
            with self.subTest(op=op, start=start):
                self.assertTrue(SemanticAssertionsGate().evaluate(
                    temporal_contract(op, start, days), Proposal(result={'value': expected})).passed)

    def test_array_pointer_rejects_non_ascii_digits_and_noncanonical_indices(self):
        for token in ('\u00b2', '\u0661', '\uff11', '0\u0661', '\u0660', '01', '-1', '+1', '', '-'):
            with self.subTest(token=token), self.assertRaises(SemanticEvaluationError) as caught:
                _resolve_pointer([10, 20, 30], '/' + token, 'result')
            self.assertEqual(caught.exception.code, 'reference.invalid_index')

    def test_repair_pointer_applies_identical_index_rules_at_leaf_and_parent(self):
        for token in ('\u00b2', '\u0661', '\uff11', '01'):
            for value, pointer in (([10, 20, 30], '/' + token),
                                   ([{'value': 10}, {'value': 20}], '/' + token + '/value')):
                before = copy.deepcopy(value)
                with self.subTest(pointer=pointer), self.assertRaises(SemanticEvaluationError) as caught:
                    _replace_existing_pointer(value, pointer, 999)
                self.assertEqual(caught.exception.code, 'reference.invalid_index')
                self.assertEqual(value, before)

    def test_unicode_object_keys_and_ascii_array_indices_remain_valid(self):
        value = {'\u0661': [10, {'\u00b2': 30}]}
        self.assertEqual(_resolve_pointer(value, '/\u0661/1/\u00b2', 'result'), 30)
        self.assertEqual(_replace_existing_pointer(value, '/\u0661/1/\u00b2', 31),
                         {'\u0661': [10, {'\u00b2': 31}]})
        self.assertEqual(_resolve_pointer([5], '/0', 'result'), 5)

    def test_unicode_array_pointer_records_failure_instead_of_success_or_crash(self):
        for token in ('\u00b2', '\u0661'):
            inputs = {'items': [10, 20, 30], 'semantic_assertions': [assertion(
                'index-boundary', 'equals', ref('result', '/value'), ref('inputs', '/items/' + token))]}
            runtime = SyntheticRuntime({'value': 20})
            with self.subTest(token=token), EvidenceLedger() as ledger:
                result = DohaaController(runtime, (SemanticAssertionsGate(),), ledger).run(
                    semantic_contract(inputs, max_attempts=1))
                self.assertEqual(result.status, RunStatus.ESCALATED)
                self.assertEqual(result.gate_results[0].details['violations'][0]['error_code'],
                                 'reference.invalid_index')
                self.assertEqual(runtime.calls, 1)
                self.assertEqual(sum(r.event_type == 'run.finished' for r in ledger.records()), 1)
                self.assertTrue(ledger.verify_chain())


if __name__ == '__main__':
    unittest.main()
