import base64
import copy
import json
import unittest
from decimal import Decimal
from hermes_dohaa.learning import native_prompt as n, native_evaluation as e, shadow
from hermes_dohaa.learning.native_response_format import response_format_for_policy
from test_native_fixed_context import fixed_policy, fixed_wire
from test_native_shadow_adapter import encode, digest, request
import test_native_reasoning_compatibility as compatibility
import test_native_failure_diagnostics as diagnostics
import test_native_binary_reasoning as binary


def evaluation_policy(mode='off'):
    return dict(fixed_policy(mode), schema_version=e.POLICY_VERSION, response_contract=e.CONTRACT)


def evaluation_wire(policy):
    raw = fixed_wire(policy)
    raw['response_format'] = response_format_for_policy(policy)
    return raw


class NumericEvaluationTests(unittest.TestCase):
    def test_exact_decimal_values_and_boundary(self):
        for text in ('0', '-0', '-12.50', '9007199254740993', '0.00001', '9'*128):
            self.assertEqual(e.parse_numeric_answer(encode({'answer': text})), Decimal(text))

    def test_reject_ambiguous_or_non_numeric_envelopes(self):
        bad = [b'', b'{}', b'{"answer":"1","answer":"2"}', b'{"answer":"1","actions":[]}',
               b'```json\n{"answer":"1"}\n```', b'{"answer":true}', b'{"answer":1}']
        bad += [encode({'answer': s}) for s in ('NaN', 'Infinity', '1e3', '+1', '01', ' 1', '1 ',
                                              '.5', '1.', '1,000', '1/2', '9'*129, 'answer is 1')]
        for data in bad:
            with self.subTest(data=data), self.assertRaises((ValueError, shadow.ShadowError)):
                e.parse_numeric_answer(data)

    def test_new_policy_requires_exact_contract_and_preserves_old_policy(self):
        for mode in ('off', 'on'):
            p = evaluation_policy(mode)
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            n.validate_wire_request(encode(evaluation_wire(p)), request(), p)
            for contract in ('controlled-tool-proposal/1.0', 'shadow-boolean-answer/1.0', None):
                wrong = dict(p, response_contract=contract)
                with self.assertRaises(n.NativePromptError): n.validate_native_policy(encode(wrong), digest(encode(wrong)))
            wrong = dict(fixed_policy(mode), response_contract=e.CONTRACT)
            with self.assertRaises(n.NativePromptError): n.validate_native_policy(encode(wrong), digest(encode(wrong)))
            n.validate_native_policy(encode(fixed_policy(mode)), digest(encode(fixed_policy(mode))))

    def test_no_shared_mutable_schema_or_embedded_answer(self):
        schema = response_format_for_policy(evaluation_policy())
        self.assertEqual(schema, e.numeric_response_format())
        schema['json_schema']['schema']['properties']['answer']['enum'] = ['42']
        self.assertNotIn('enum', e.numeric_response_format()['json_schema']['schema']['properties']['answer'])

    def test_original_terminal_context_and_schema_are_reverified(self):
        for mode in ('off', 'on'):
            f, _, req, trace = compatibility.CompatibilityTerminalTests().fixture(mode)
            p = evaluation_policy(mode); raw = evaluation_wire(p)
            trace.update(runtime_policy_sha256=digest(encode(p)),
                         wire_request_base64=base64.b64encode(encode(raw)).decode())
            self.assertEqual(json.loads(f.verify(p, req, trace))['status'], 'completed')
            for changed in ('context', 'schema'):
                altered = copy.deepcopy(raw)
                if changed == 'context': altered['messages'][0]['content'] += '\nworker identity'
                else: altered['response_format']['json_schema']['schema']['properties']['answer']['enum'] = ['42']
                trace['wire_request_base64'] = base64.b64encode(encode(altered)).decode()
                with self.assertRaises(n.NativePromptError): f.verify(p, req, trace)

    def test_old_selector_cannot_silently_accept_new_domain(self):
        f = compatibility.CompatibilitySelectorTests().fixture()
        f.policies = {'none': evaluation_policy(), 'medium': evaluation_policy('on')}
        args = f.arguments(); library = json.loads(args['profiles_bytes'])
        library['schema_version'] = 'hermes-reasoning-profiles/1.3'
        args.update(profiles_bytes=encode(library), profiles_sha256=digest(encode(library)))
        with self.assertRaisesRegex(ValueError, 'evaluation_requires_separate_planner'): f.m.build_plan(**args)


class NumericGuardTests(unittest.TestCase):
    def setUp(self):
        diagnostics.GuardDiagnosticsTests.setUp(self)
        self.guard.policy = evaluation_policy()

    def test_schema_substitution_denied_before_sender(self):
        binary.BinaryGuardTests.metadata(self)
        self.transport.reset_mock()
        raw = evaluation_wire(self.guard.policy)
        raw['response_format']['json_schema']['schema']['properties']['answer']['enum'] = ['42']
        self.request.content = encode(raw)
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.transport.assert_not_called()

    def test_one_generation_and_catalog_guard_apply(self):
        self.request.content = encode(evaluation_wire(self.guard.policy))
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.transport.assert_not_called()
        binary.BinaryGuardTests.metadata(self)
        from test_native_shadow_adapter import response
        self.transport.return_value = diagnostics.Stream([encode(response())])
        self.client.send(self.request)
        with self.assertRaises(binary.w.BudgetStop): self.client.send(self.request)
        self.assertEqual(self.guard.posts, 1)
