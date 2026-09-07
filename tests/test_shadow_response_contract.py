import json
import unittest

from hermes_dohaa.learning.shadow import ShadowError, admit_shadow_response, render_shadow_request


FIELDS = {'total': 'integer', 'ids': 'array'}


class ShadowResponseContractTests(unittest.TestCase):
    def test_request_makes_sibling_actions_raw_json_and_declared_fields_explicit(self):
        task = {'instructions': 'Sum the supplied values.', 'values': [3, 7]}
        message = json.loads(render_shadow_request(json.dumps(task).encode(), result_fields=FIELDS))
        self.assertEqual(message['task'], task)
        contract = message['response_contract']
        self.assertEqual(contract['top_level_keys'], ['result', 'actions'])
        self.assertIn('No Markdown', contract['encoding'])
        self.assertIn('top-level sibling', contract['placement'])
        self.assertEqual(contract['result']['required_fields'], FIELDS)
        self.assertNotIn('expected_result', message)
        self.assertNotIn('answer', contract)

    def test_exact_envelope_is_admitted_without_correctness_judgment(self):
        raw = {'result': {'total': -999, 'ids': []}, 'actions': []}
        result = admit_shadow_response(json.dumps(raw), result_fields=FIELDS)
        self.assertEqual(result, {'outcome': {'status': 'completed', **raw}, 'feedback': []})

    def test_markdown_gets_specific_feedback_and_is_not_unwrapped(self):
        result = admit_shadow_response('```json\n{"result":{"total":42,"ids":[]},"actions":[]}\n```', result_fields=FIELDS)
        self.assertEqual(result['outcome'], {'status': 'failed', 'error_code': 'invalid_response'})
        self.assertEqual(result['feedback'], [{'code': 'shadow_response.markdown_fence'}])
        self.assertNotIn('42', json.dumps(result))

    def test_nested_actions_get_specific_feedback_without_moving_fields(self):
        raw = {'result': {'total': 42, 'ids': [], 'actions': []}}
        result = admit_shadow_response(json.dumps(raw), result_fields=FIELDS)
        self.assertEqual(result['feedback'], [{'code': 'shadow_response.actions_not_top_level'}])
        self.assertEqual(result['outcome']['status'], 'failed')
        self.assertNotIn('42', json.dumps(result))

    def test_extra_or_missing_fields_and_wrong_types_are_failures(self):
        cases = [({'result': {}, 'actions': []}, 'shadow_response.result_fields'),
                 ({'result': {'total': True, 'ids': []}, 'actions': []}, 'shadow_response.result_type'),
                 ({'result': {'total': 3.0, 'ids': []}, 'actions': []}, 'shadow_response.result_type'),
                 ({'result': {'total': 3, 'ids': []}, 'actions': {},}, 'shadow_response.actions_invalid'),
                 ({'result': {'total': 3, 'ids': []}, 'actions': [], 'approved': True}, 'shadow_response.top_level_fields')]
        for raw, code in cases:
            with self.subTest(code=code):
                result = admit_shadow_response(json.dumps(raw), result_fields=FIELDS)
                self.assertEqual(result['feedback'], [{'code': code}])
                self.assertEqual(result['outcome']['status'], 'failed')

    def test_proposed_actions_are_retained_for_the_scorer_and_not_executed(self):
        action = 'private action contents'
        result = admit_shadow_response(json.dumps({'result': {'total': 3, 'ids': []}, 'actions': [action]}), result_fields=FIELDS)
        self.assertEqual(result['outcome']['actions'], [action])
        self.assertEqual(result['outcome']['status'], 'completed')
        self.assertEqual(result['feedback'], [{'code': 'shadow_response.actions_proposed'}])
        self.assertNotIn(action, json.dumps(result['feedback']))

    def test_duplicates_nonfinite_and_unicode_keep_safe_diagnostics(self):
        for raw, code in (('{"result":{},"result":{}}', 'shadow.duplicate_key'),
                          ('{"result":NaN}', 'shadow.nonfinite'),
                          ('{"result":1e999}', 'shadow.nonfinite'),
                          ('\ud800', 'shadow_response.unicode_invalid')):
            result = admit_shadow_response(raw, result_fields=FIELDS)
            self.assertEqual(result['outcome']['status'], 'failed')
            self.assertEqual(result['feedback'], [{'code': code}])

    def test_contract_rejects_values_instead_of_types_reserved_actions_and_empty_fields(self):
        for fields in ({}, {'total': 42}, {'total': 'arbitrary'}, {'actions': 'array'}, {'bad key': 'integer'}):
            with self.assertRaises(ShadowError):
                render_shadow_request(b'{}', result_fields=fields)
            with self.assertRaises(ShadowError):
                admit_shadow_response('{}', result_fields=fields)
