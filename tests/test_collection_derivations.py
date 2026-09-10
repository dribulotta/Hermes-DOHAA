import json
import unittest
from pathlib import Path

from hermes_dohaa.assurance.gates import SemanticAssertionsGate
from hermes_dohaa.assurance.semantic_assertions import (
    MAX_COLLECTION_ITEMS, parse_semantic_assertions,
)
from hermes_dohaa.cli import _contract_gates, _validate_contract_gate_inputs
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.semantic_repair import propose_deterministic_semantic_repair
from hermes_dohaa.runtime.base import Proposal
from test_semantic_assertions import assertion, expression, ref, semantic_contract


def check(operation, values, expected):
    inputs = {f'arg{i}': value for i, value in enumerate(values)}
    inputs['semantic_assertions'] = [assertion(
        'derived', 'equals', ref('result', '/value'),
        expression(operation, *(ref('inputs', f'/arg{i}') for i in range(len(values)))))]
    parse_semantic_assertions(inputs['semantic_assertions'])
    return SemanticAssertionsGate().evaluate(semantic_contract(inputs), Proposal(result={'value': expected}))


class CollectionDerivationTests(unittest.TestCase):
    def test_lookup_preserves_requested_order_and_repeated_keys(self):
        self.assertTrue(check('lookup_many', [{'b': 13, 'a': 7}, ['b', 'a', 'b']], [13, 7, 13]).passed)

    def test_lookup_uses_literal_keys_not_pointers(self):
        self.assertTrue(check('lookup_many', [{'a/~b': 9, 'ñ': 4}, ['ñ', 'a/~b']], [4, 9]).passed)

    def test_lookup_missing_key_fails_without_disclosing_it(self):
        result = check('lookup_many', [{'known': 4}, ['PRIVATE_MISSING_KEY']], [])
        self.assertFalse(result.passed)
        self.assertNotIn('PRIVATE_MISSING_KEY', json.dumps(result.to_dict()))

    def test_lookup_rejects_boolean_numeric_and_container_keys(self):
        for key in [True, 1, [], {}]:
            with self.subTest(key=key):
                self.assertFalse(check('lookup_many', [{'1': 8, 'true': 9}, [key]], [8]).passed)

    def test_lookup_requires_object_and_array(self):
        for values in [[[], []], [{'x': 4}, 'x']]:
            with self.subTest(values=values):
                self.assertFalse(check('lookup_many', values, []).passed)

    def test_empty_lookup_and_dot_product(self):
        self.assertTrue(check('lookup_many', [{}, []], []).passed)
        self.assertTrue(check('dot_product', [[], []], 0).passed)

    def test_dot_product_rejects_truncating_mismatched_lengths(self):
        self.assertFalse(check('dot_product', [[3, 8], [10]], 30).passed)

    def test_dot_product_multiplies_each_pair(self):
        self.assertTrue(check('dot_product', [[2, 3, -1], [7, 11, 5]], 42).passed)
        self.assertFalse(check('dot_product', [[2, 3, -1], [7, 11, 5]], 43).passed)

    def test_dot_product_rejects_non_numbers(self):
        for item in [True, '3', None, [], {}]:
            with self.subTest(item=item):
                self.assertFalse(check('dot_product', [[item], [2]], 2).passed)

    def test_dot_product_bounds_intermediate_product_and_sum(self):
        self.assertFalse(check('dot_product', [[10**100], [2]], 0).passed)
        self.assertFalse(check('dot_product', [[10**100, 10**100], [1, 1]], 0).passed)

    def test_keys_distinguishes_absent_from_present_null(self):
        self.assertTrue(check('keys', [{'x': None, 'y': False}], ['x', 'y']).passed)
        self.assertFalse(check('keys', [[1, 2]], ['0', '1']).passed)

    def test_difference_preserves_order_and_duplicates(self):
        self.assertTrue(check('difference', [['b', 'a', 'b', 'c'], ['a']], ['b', 'b', 'c']).passed)

    def test_difference_uses_strict_json_equality(self):
        self.assertTrue(check('difference', [[True, 1, 1.0, '1'], [1]], [True, 1.0, '1']).passed)
        self.assertTrue(check('difference', [[{'a': 1, 'b': 2}], [{'b': 2, 'a': 1}]], []).passed)

    def test_difference_requires_arrays(self):
        self.assertFalse(check('difference', [['x'], {'x': 1}], ['x']).passed)

    def test_new_operators_enforce_arity_and_fields(self):
        for op, count in [('keys', 1), ('difference', 2), ('lookup_many', 2), ('dot_product', 2)]:
            for args in [[], [ref('inputs', '/v')]*(count+1)]:
                with self.subTest(op=op, count=len(args)):
                    with self.assertRaises(ValueError):
                        parse_semantic_assertions([assertion('x','equals',ref('result','/x'),expression(op,*args))])
            with self.subTest(op=op, unknown_field=True):
                with self.assertRaises(ValueError):
                    parse_semantic_assertions([assertion('x','equals',ref('result','/x'),
                        expression(op, *[ref('inputs','/v')]*count, code='forbidden'))])

    def test_new_operators_keep_collection_limits(self):
        large = list(range(MAX_COLLECTION_ITEMS+1))
        for op, values in [('keys', [{str(i): i for i in large}]),
                           ('lookup_many', [{'x': 2}, ['x']*len(large)]),
                           ('difference', [large, []]), ('difference', [[], large]),
                           ('dot_product', [large, large])]:
            with self.subTest(op=op):
                self.assertFalse(check(op, values, []).passed)

    def test_operations_cannot_access_reserved_oracle_inputs(self):
        with self.assertRaises(ValueError):
            parse_semantic_assertions([assertion('x','equals',ref('result','/x'),
                expression('keys',ref('inputs','/expected_result')))])

    def test_join_repair_uses_data_without_mutating_original_proposal(self):
        root = Path(__file__).resolve().parents[1]/'examples/collection-derivations'
        contract = TaskContract.from_dict(json.loads((root/'relational-join.contract.json').read_text()))
        original = Proposal(result={'total': 177})
        repaired = propose_deterministic_semantic_repair(contract, original)
        self.assertIsNotNone(repaired)
        self.assertEqual(original.result, {'total': 177})
        self.assertEqual(repaired.proposal.result, {'total': 117})
        self.assertTrue(all(g.evaluate(contract, repaired.proposal).passed for g in _contract_gates(contract)))

    def test_unknown_join_key_cannot_be_guessed_by_repair(self):
        root = Path(__file__).resolve().parents[1]/'examples/collection-derivations'
        raw = json.loads((root/'relational-join.contract.json').read_text())
        raw['inputs']['orders'][0]['code'] = 'not-in-price-table'
        contract = TaskContract.from_dict(raw)
        self.assertIsNone(propose_deterministic_semantic_repair(contract, Proposal(result={'total': 117})))

    def test_public_derivation_examples_accept_reference_reject_mutations(self):
        root = Path(__file__).resolve().parents[1]/'examples/collection-derivations'
        cases = json.loads((root/'regressions.json').read_text())['cases']
        self.assertEqual(len(cases), 5)
        for case in cases:
            with self.subTest(case=case['contract_file']):
                contract = TaskContract.from_dict(json.loads((root/case['contract_file']).read_text()))
                _validate_contract_gate_inputs(contract)
                gates = _contract_gates(contract)
                self.assertTrue(all(g.evaluate(contract, Proposal(result=case['reference'])).passed for g in gates))
                for wrong in case['incorrect_results']:
                    self.assertFalse(all(g.evaluate(contract, Proposal(result=wrong)).passed for g in gates))


if __name__ == '__main__':
    unittest.main()
