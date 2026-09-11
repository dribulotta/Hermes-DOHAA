import copy
import json
import unittest

from hermes_dohaa.learning import native_prompt as native, shadow
from hermes_dohaa.learning.native_worker import native_model_configuration
from hermes_dohaa.learning.native_response_format import (
    BOOLEAN_RESPONSE_CONTRACT as BOOLEAN, PROMPT_RESPONSE_CONTRACT as PROMPT,
    fixed_shadow_result_fields, response_format_for_policy, native_request_overrides)
from test_native_shadow_adapter import policy, request, wire, encode, digest


class FixedShadowContractTests(unittest.TestCase):
    def configured(self, contract=BOOLEAN):
        return dict(policy(), schema_version='hermes-native-shadow-policy/1.2',
                    response_contract=contract, context_length=16384)

    def test_explicit_fixed_contracts_are_valid_only_with_context_policy(self):
        for contract in (BOOLEAN, PROMPT):
            p=self.configured(contract)
            self.assertEqual(native.validate_native_policy(encode(p),digest(encode(p))),p)
            self.assertEqual(native_model_configuration(p)['context_length'],16384)
            for legacy in ('hermes-native-shadow-policy/1.0','hermes-native-shadow-policy/1.1'):
                old=dict(p,schema_version=legacy);old.pop('context_length')
                with self.subTest(contract=contract,version=legacy),self.assertRaises((native.NativePromptError,shadow.ShadowError)):
                    native.validate_native_policy(encode(old),digest(encode(old)))

    def test_unknown_contract_and_caller_schema_are_not_accepted(self):
        for value in ('shadow-arbitrary/1.0', {'answer': 'boolean'}):
            p=self.configured(value)
            with self.assertRaises((native.NativePromptError,shadow.ShadowError)):
                native.validate_native_policy(encode(p),digest(encode(p)))
        p=self.configured();p['response_schema']={'type':'object'}
        with self.assertRaises(shadow.ShadowError):
            native.validate_native_policy(encode(p),digest(encode(p)))

    def test_wire_must_match_the_selected_fixed_schema_exactly(self):
        for contract, other in ((BOOLEAN,PROMPT),(PROMPT,BOOLEAN)):
            p=self.configured(contract);logical=request();body=wire(logical,p)
            body.update(native_request_overrides(p))
            native.validate_wire_request(encode(body),logical,p)
            changed=copy.deepcopy(body)
            changed['response_format']=response_format_for_policy(self.configured(other))
            with self.subTest(contract=contract),self.assertRaises(native.NativePromptError):
                native.validate_wire_request(encode(changed),logical,p)
            body['response_format']['json_schema']['schema']['additionalProperties']=True
            with self.assertRaises(native.NativePromptError):
                native.validate_wire_request(encode(body),logical,p)

    def test_schema_copies_cannot_mutate_future_policy_or_allow_actions(self):
        for contract in (BOOLEAN,PROMPT):
            p=self.configured(contract);first=response_format_for_policy(p)
            first['json_schema']['schema']['properties']['actions']['maxItems']=10
            fresh=response_format_for_policy(p)['json_schema']['schema']
            self.assertEqual(fresh['properties']['actions']['maxItems'],0)
            self.assertFalse(fresh['additionalProperties'])
            self.assertFalse(fresh['properties']['result']['additionalProperties'])
            fields=fixed_shadow_result_fields(contract);fields['unexpected']='string'
            self.assertNotIn('unexpected',fixed_shadow_result_fields(contract))

    def test_boolean_shape_does_not_confer_truth_and_local_type_check_is_strict(self):
        fields=fixed_shadow_result_fields(BOOLEAN)
        for value in (True,False):
            outcome=shadow.admit_shadow_response(json.dumps({'result':{'answer':value},'actions':[]}),result_fields=fields)['outcome']
            self.assertEqual(outcome['status'],'completed')
            self.assertIs(outcome['result']['answer'],value)
        for value in (1,'true',None,{},[]):
            outcome=shadow.admit_shadow_response(json.dumps({'result':{'answer':value},'actions':[]}),result_fields=fields)['outcome']
            self.assertNotEqual(outcome['status'],'completed')

    def test_prompt_schema_contains_only_public_shape_not_quality_or_adoption_rules(self):
        p=self.configured(PROMPT);schema=response_format_for_policy(p)['json_schema']['schema']
        self.assertEqual(set(schema['properties']),{'result','actions'})
        self.assertEqual(schema['properties']['result']['properties'],
                         {'artifact':{'type':'string'},'rationale':{'type':'string'}})
        self.assertNotIn('expected_answer',json.dumps(schema))
        self.assertNotIn('reasoning_effort',native_request_overrides(p))
        self.assertEqual(p['reasoning_effort'],'none')


if __name__=='__main__':
    unittest.main()
