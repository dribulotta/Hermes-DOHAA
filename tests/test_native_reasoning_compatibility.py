"""Separate binary intent, compatible wire and unverified effective setting."""
import base64
import json
import os
import unittest
from unittest.mock import patch

from hermes_dohaa.learning import native_prompt as n, native_worker as w, shadow
from hermes_dohaa.learning.native_response_format import native_request_overrides
from hermes_dohaa.learning.native_reasoning import binary_model_sha256, verify_binary_catalog
from test_native_binary_reasoning import binary_policy, binary_wire, catalog
import test_native_binary_reasoning as binary_tests
import test_native_failure_diagnostics as diagnostics
from test_native_shadow_adapter import encode, digest, request, response

VERSION = 'hermes-native-tool-policy/1.2'
CONTRACT = 'lmstudio-binary-via-generic-effort/1.0'


def compat_policy(mode='off'):
    return dict(binary_policy(), schema_version=VERSION, reasoning_intent=mode,
        reasoning_effort={'off': 'none', 'on': 'medium'}[mode], reasoning_contract=CONTRACT)


class CompatibilityPolicyTests(unittest.TestCase):
    def test_both_modes_bind_separate_intent_and_wire(self):
        from hermes_dohaa.learning.native_reasoning import reasoning_enabled, selector_profile
        for mode, wire, enabled in (('off', 'none', False), ('on', 'medium', True)):
            p = compat_policy(mode)
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            self.assertEqual(native_request_overrides(p)['reasoning_effort'], wire)
            self.assertIs(reasoning_enabled(p), enabled)
            self.assertEqual(selector_profile(p), wire)
            self.assertEqual(w.native_model_configuration(p)['provider'], 'lmstudio')

    def test_raw_binary_values_cannot_reach_compatibility_endpoint(self):
        for mode in ('off', 'on'):
            p = compat_policy(mode)
            for invalid in ('off', 'on', None, True, '', 'high'):
                changed = dict(p, reasoning_effort=invalid)
                with self.assertRaises((ValueError, n.NativePromptError)):
                    n.validate_native_policy(encode(changed), digest(encode(changed)))

    def test_mismatched_or_missing_contract_and_intent_fail_closed(self):
        p = compat_policy()
        for field, value in (('reasoning_intent', 'on'), ('reasoning_intent', None),
                             ('reasoning_contract', None), ('reasoning_contract', 'implicit-fallback')):
            changed = dict(p, **{field: value})
            with self.assertRaises((ValueError, n.NativePromptError)):
                n.validate_native_policy(encode(changed), digest(encode(changed)))
            with self.assertRaises(ValueError): native_request_overrides(changed)
        for field in ('reasoning_intent', 'reasoning_contract'):
            changed = dict(p); del changed[field]
            with self.assertRaises((ValueError, n.NativePromptError)):
                n.validate_native_policy(encode(changed), digest(encode(changed)))

    def test_known_default_on_dependency_is_explicit_not_silently_assumed(self):
        for mode in ('off', 'on'):
            p = compat_policy(mode); verify_binary_catalog(encode(catalog()), p)
            changed = catalog(); changed['models'][0]['capabilities']['reasoning']['default'] = 'off'
            # Even a newly committed default-off model cannot satisfy this contract.
            p['reasoning_model_sha256'] = binary_model_sha256(encode(changed), p['model'])
            with self.assertRaises(ValueError): verify_binary_catalog(encode(changed), p)

    def test_wire_cannot_contradict_intent_or_sampling_and_schema(self):
        for mode in ('off', 'on'):
            p = compat_policy(mode); raw = binary_wire(p)
            self.assertEqual(n.validate_wire_request(encode(raw), request(), p), raw)
            for change in ({'reasoning_effort': mode}, {'think': mode != 'on'}, {'max_tokens': 999},
                           {'reasoning_intent': mode}, {'response_format': {'type': 'json_object'}}):
                with self.assertRaises(n.NativePromptError): n.validate_wire_request(encode(dict(raw, **change)), request(), p)
        p = compat_policy(); p['reasoning_intent'] = 'on'
        with self.assertRaises(n.NativePromptError): n.validate_wire_request(encode(binary_wire(p)), request(), p)

    def test_evidence_describes_dependency_without_claiming_server_setting(self):
        from hermes_dohaa.learning.native_reasoning import reasoning_request_evidence
        for mode, wire in (('off', 'none'), ('on', 'medium')):
            evidence = reasoning_request_evidence(compat_policy(mode))
            self.assertEqual(evidence, dict(schema_version='hermes-reasoning-request-evidence/1.0',
                intent=mode, wire_effort=wire, on_depends_on_declared_default=mode == 'on',
                model_default='on', effective_mode_attested=False))

    def test_historical_binary_and_generic_policies_are_not_rewritten(self):
        for mode in ('off', 'on'):
            p = binary_policy(mode)
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            self.assertEqual(native_request_overrides(p)['reasoning_effort'], mode)


class CompatibilityTransportTests(unittest.TestCase):
    def setUp(self):
        diagnostics.GuardDiagnosticsTests.setUp(self)
        self.guard.policy = compat_policy()
        self.request.content = encode(binary_wire(self.guard.policy))

    def metadata(self):
        binary_tests.BinaryGuardTests.metadata(self)

    def test_no_generation_without_exact_catalog(self):
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.assertEqual(self.guard.posts, 0); self.transport.assert_not_called()

    def test_compatible_values_use_existing_transport_once(self):
        for mode in ('off', 'on'):
            self.guard.policy = compat_policy(mode); self.guard.posts = 0
            self.request.content = encode(binary_wire(self.guard.policy))
            self.metadata(); self.transport.return_value = diagnostics.Stream([encode(response())])
            self.client.send(self.request)
            self.assertEqual(self.guard.posts, 1)
            with self.assertRaises(w.BudgetStop): self.client.send(self.request)
        self.assertEqual(self.transport.call_count, 4)

    def test_observed_400_rejection_never_becomes_success_or_retry(self):
        self.metadata()
        # Public, synthetic copy of the exact parameter rejection; no private prompt.
        error = {'error': {'message': "Invalid 'reasoning_effort' value: 'off'. Supported values: none, minimal, low, medium, high, xhigh.",
                           'type': 'invalid_request_error', 'param': 'reasoning_effort', 'code': 'invalid_value'}}
        self.transport.return_value = diagnostics.Stream([encode(error)], status=400)
        with self.assertRaisesRegex(n.NativePromptError, 'http_status'): self.client.send(self.request)
        self.assertFalse(self.guard.server_finished)
        self.assertEqual(self.guard.response_bytes, encode(error))
        self.assertEqual(self.guard.generation_failure['http_status'], 400)
        with self.assertRaises(w.BudgetStop): self.client.send(self.request)
        self.assertEqual(self.transport.call_count, 2)


class CompatibilityTerminalTests(unittest.TestCase):
    def fixture(self, mode='off'):
        from hermes_dohaa.learning.native_reasoning import reasoning_request_evidence
        f = binary_tests.BinaryTerminalTests(); _, req, trace = f.trace(mode, reasoning=mode == 'on')
        p = compat_policy(mode)
        trace.update(runtime_policy_sha256=digest(encode(p)),
            wire_request_base64=base64.b64encode(encode(binary_wire(p))).decode(),
            reasoning_request_evidence=reasoning_request_evidence(p))
        return f, p, req, trace

    def test_terminal_rechecks_declared_intent_wire_and_default_dependency(self):
        for mode in ('off', 'on'):
            f, p, req, trace = self.fixture(mode)
            self.assertEqual(json.loads(f.verify(p, req, trace))['status'], 'completed')

    def test_missing_or_fabricated_mode_attestation_rejected(self):
        for field, value in (('effective_mode_attested', True), ('on_depends_on_declared_default', False),
                             ('intent', 'off'), ('wire_effort', 'on'), ('model_default', 'off')):
            f, p, req, trace = self.fixture('on'); trace['reasoning_request_evidence'][field] = value
            with self.assertRaises(n.NativePromptError): f.verify(p, req, trace)
        f, p, req, trace = self.fixture(); del trace['reasoning_request_evidence']
        with self.assertRaises(n.NativePromptError): f.verify(p, req, trace)

    def test_off_with_returned_reasoning_remains_mismatch(self):
        f, p, req, trace = self.fixture()
        resp = response(); resp['choices'][0]['message']['reasoning_content'] = 'synthetic'
        trace['wire_response_base64'] = base64.b64encode(encode(resp)).decode()
        with self.assertRaisesRegex(n.NativePromptError, 'reasoning_mismatch'): f.verify(p, req, trace)


class CompatibilitySelectorTests(unittest.TestCase):
    def fixture(self):
        from test_prospective_reasoning_budget import BudgetPlannerTests
        f = BudgetPlannerTests(); f.setUp()
        f.policies = {'none': compat_policy('off'), 'medium': compat_policy('on')}
        return f

    def arguments(self, f):
        args = f.arguments(); library = json.loads(args['profiles_bytes'])
        library['schema_version'] = 'hermes-reasoning-profiles/1.2'
        args['profiles_bytes'] = encode(library); args['profiles_sha256'] = digest(args['profiles_bytes'])
        return args

    def test_plan_exposes_intent_wire_and_default_dependency_at_same_budget(self):
        f = self.fixture(); args = self.arguments(f); plan = json.loads(f.m.build_plan(**args))
        self.assertEqual(plan['schema_version'], 'hermes-reasoning-budget-plan/1.2')
        self.assertEqual([(e['reasoning_mode'], e['reasoning_effort'], e['on_depends_on_declared_default'])
                         for e in plan['entries']], [('off', 'none', False), ('on', 'medium', True)])
        self.assertEqual(plan['reserved_generation_tokens'], 3*512)
        with self.assertRaisesRegex(ValueError, 'generation_ceiling_exceeded'):
            f.m.build_plan(**dict(args, token_ceiling=3*512-1))

    def test_mixed_versions_or_nonfactor_binding_changes_rejected(self):
        for change in ({'reasoning_model_sha256': 'f'*64}, {'context_length': 4096}, {'reasoning_contract': 'implicit'}):
            f = self.fixture(); f.policies['medium'].update(change)
            with self.assertRaises((ValueError, n.NativePromptError)): f.m.build_plan(**self.arguments(f))
        f = self.fixture(); f.policies['medium'] = binary_policy('on')
        with self.assertRaises(ValueError): f.m.build_plan(**self.arguments(f))


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged Linux evidence boundary')
class CompatibilityHostTests(unittest.TestCase):
    def test_both_compatible_modes_preserve_host_recovery_and_exact_owned_unload(self):
        import test_native_tool_evidence as fixtures
        from hermes_dohaa.learning.native_reasoning import reasoning_request_evidence
        for mode in ('off', 'on'):
            fixture = fixtures.ToolEvidenceTests(); self.addCleanup(fixture.doCleanups)
            with patch.object(fixtures, 'tool_policy', lambda: compat_policy(mode)): fixture.setUp()
            fixture.trace_change = lambda trace: trace.update(
                reasoning_catalog_base64=base64.b64encode(encode(catalog())).decode(),
                reasoning_preflight_passed=True, reasoning_request_evidence=reasoning_request_evidence(fixture.p))
            fixture.test_verified_native_artifact_and_ownership_recover_host_without_resend()
            fixture.lease.finish()
            self.assertEqual(fixture.instances, [])
            self.assertEqual(fixture.sends, 1)


if __name__ == '__main__':
    unittest.main()
