"""Binary intent must survive native translation with pre-dispatch evidence."""
import base64
import copy
import json
import os
import types
import unittest
from unittest.mock import patch

from hermes_dohaa.learning import native_prompt as n, native_worker as w, shadow
from hermes_dohaa.learning.native_response_format import native_request_overrides
from hermes_dohaa.learning.native_tool_contract import tool_response_format
from test_native_shadow_adapter import policy, request, wire, response, encode, digest
import test_native_failure_diagnostics as diagnostics
from test_native_failure_diagnostics import Stream

VERSION = 'hermes-native-tool-policy/1.1'


def catalog():
    return {'models': [{'key': policy()['model'], 'type': 'llm', 'format': 'gguf',
        'selected_variant': 'synthetic-q5', 'loaded_instances': [],
        'capabilities': {'reasoning': {'allowed_options': ['off', 'on'], 'default': 'on'}}}]}


def binary_policy(mode='off'):
    p = policy()
    identity = {k: v for k, v in catalog()['models'][0].items() if k != 'loaded_instances'}
    p.update(schema_version=VERSION, reasoning_effort=mode, context_length=8192,
             response_contract='controlled-tool-proposal/1.0',
             reasoning_model_sha256=digest(encode(identity)))
    return p


def binary_wire(p):
    return dict(wire(p=p), response_format=tool_response_format())


class BinaryPolicyTests(unittest.TestCase):
    def test_both_binary_modes_are_explicit_versioned_policies(self):
        for mode in ('off', 'on'):
            p = binary_policy(mode)
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            self.assertEqual(native_request_overrides(p), dict(
                seed=p['seed'], temperature=p['temperature'], top_p=p['top_p'],
                response_format=tool_response_format(), reasoning_effort=mode))
            self.assertEqual(w.native_model_configuration(p)['provider'], 'lmstudio')

    def test_legacy_modes_and_overrides_are_unchanged(self):
        for mode in ('none', 'medium', 'high'):
            p = policy(); p['reasoning_effort'] = mode
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            self.assertNotIn('reasoning_effort', native_request_overrides(p))
            self.assertEqual(n.validate_wire_request(encode(wire(p=p)), request(), p), wire(p=p))

    def test_no_implicit_upgrade_or_generic_fallback(self):
        for mode in ('none', 'medium', None, True, 'ON', ''):
            p = binary_policy(mode)
            with self.subTest(mode=mode), self.assertRaises((ValueError, n.NativePromptError, shadow.ShadowError)):
                n.validate_native_policy(encode(p), digest(encode(p)))
        p = binary_policy(); p['schema_version'] = 'hermes-native-tool-policy/1.0'
        with self.assertRaises((n.NativePromptError, shadow.ShadowError)):
            n.validate_native_policy(encode(p), digest(encode(p)))

    def test_capability_commitment_is_required_and_bounded(self):
        for value in (None, True, 'x'*64, 'f'*63):
            p = binary_policy(); p['reasoning_model_sha256'] = value
            with self.assertRaises((ValueError, n.NativePromptError, shadow.ShadowError)):
                n.validate_native_policy(encode(p), digest(encode(p)))
        p = binary_policy(); del p['reasoning_model_sha256']
        with self.assertRaises((n.NativePromptError, shadow.ShadowError)):
            n.validate_native_policy(encode(p), digest(encode(p)))

    def test_wire_preserves_binary_intent_and_rejects_translated_or_conflicting_values(self):
        for mode in ('off', 'on'):
            p = binary_policy(mode); raw = binary_wire(p)
            self.assertEqual(n.validate_wire_request(encode(raw), request(), p), raw)
            n.validate_wire_request(encode(dict(raw, think=mode == 'on')), request(), p)
            variants = [dict(raw, reasoning_effort=v) for v in ('none', 'medium', 'off' if mode == 'on' else 'on')]
            variants += [dict(raw, think=mode != 'on'), dict(raw, think=0), dict(raw, max_tokens=513),
                         dict(raw, response_format={'type': 'json_object'}), dict(raw, extra_body={})]
            for changed in variants:
                with self.assertRaises(n.NativePromptError):
                    n.validate_wire_request(encode(changed), request(), p)

    def test_internal_enabled_and_profile_labels_match_binary_intent(self):
        from hermes_dohaa.learning.native_reasoning import reasoning_enabled, selector_profile
        for mode, enabled, label in (('off', False, 'none'), ('on', True, 'medium')):
            self.assertIs(reasoning_enabled(binary_policy(mode)), enabled)
            self.assertEqual(selector_profile(binary_policy(mode)), label)

    def test_binary_profile_keeps_context_and_tool_isolation_checks(self):
        from test_native_lmstudio_context import agent
        p = binary_policy(); a = agent(p)
        self.assertTrue(w.model_profile_checks(a, p))
        for field, value in (('provider', 'custom'), ('model', 'alias'), ('tools', ['reserve']),
                             ('_config_context_length', 4096)):
            a = agent(p); setattr(a, field, value)
            self.assertFalse(w.model_profile_checks(a, p))


class BinaryCatalogTests(unittest.TestCase):
    def check(self, value, p=None):
        from hermes_dohaa.learning.native_reasoning import verify_binary_catalog
        return verify_binary_catalog(encode(value), p or binary_policy())

    def test_exact_key_and_stable_metadata_commitment(self):
        self.check(catalog())
        changed = catalog(); changed['models'][0]['loaded_instances'] = [{'id': 'owned', 'config': {}}]
        self.check(changed)

    def test_no_fuzzy_alias_or_loaded_instance_substitution(self):
        for value in ('alias', policy()['model'] + '-other'):
            changed = catalog(); changed['models'][0]['key'] = value
            changed['models'][0]['loaded_instances'] = [{'id': policy()['model']}]
            with self.assertRaises(ValueError): self.check(changed)

    def test_missing_nonbinary_duplicate_or_conflicting_capabilities_rejected(self):
        for caps in ({}, {'reasoning': None}, {'reasoning': {'allowed_options': ['on'], 'default': 'on'}},
                     {'reasoning': {'allowed_options': ['off', 'on', 'on'], 'default': 'on'}},
                     {'reasoning': {'allowed_options': ['off', 'on'], 'default': 'medium'}}):
            changed = catalog(); changed['models'][0]['capabilities'] = caps
            with self.assertRaises(ValueError): self.check(changed)

    def test_changed_variant_capabilities_and_identity_rejected_even_with_binary_options(self):
        for key, value in (('selected_variant', 'other'), ('format', 'mlx'), ('size_bytes', 7)):
            changed = catalog(); changed['models'][0][key] = value
            with self.assertRaises(ValueError): self.check(changed)
        changed = catalog(); changed['models'][0]['capabilities']['reasoning']['default'] = 'off'
        with self.assertRaises(ValueError): self.check(changed)

    def test_duplicate_keys_and_cross_model_alias_collisions_rejected(self):
        changed = catalog(); changed['models'] *= 2
        with self.assertRaises(ValueError): self.check(changed)
        for field in ('id', 'loaded_instances'):
            changed = catalog(); other = copy.deepcopy(changed['models'][0]); other['key'] = 'other'
            other[field] = policy()['model'] if field == 'id' else [{'id': policy()['model']}]
            changed['models'].append(other)
            with self.assertRaises(ValueError): self.check(changed)

    def test_bounded_bytes_model_count_and_duplicate_json_keys(self):
        from hermes_dohaa.learning.native_reasoning import verify_binary_catalog
        for raw in (b'x'*(n.MAX_WIRE+1), b'{"models":[],"models":[]}', encode({'models': catalog()['models']*257})):
            with self.assertRaises((ValueError, shadow.ShadowError)):
                verify_binary_catalog(raw, binary_policy())


class BinaryGuardTests(unittest.TestCase):
    def setUp(self):
        diagnostics.GuardDiagnosticsTests.setUp(self)
        self.guard.policy = binary_policy()
        self.request.content = encode(binary_wire(self.guard.policy))

    def metadata(self, value=None, status=200):
        self.transport.return_value = Stream([encode(value if value is not None else catalog())], status=status)
        req = types.SimpleNamespace(url=policy()['endpoint'].removesuffix('/v1')+'/api/v1/models',
            method='GET', content=b'', headers={}, extensions={})
        return self.client.send(req)

    def test_no_generation_without_validated_metadata_and_no_fallback_call(self):
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.assertEqual(self.guard.posts, 0)
        self.transport.assert_not_called()

    def test_existing_metadata_request_is_captured_before_one_generation(self):
        self.metadata()
        self.transport.return_value = Stream([encode(response())])
        self.client.send(self.request)
        self.assertEqual((self.guard.posts, self.guard.metadata, self.transport.call_count), (1, 1, 2))
        self.assertEqual(self.guard.reasoning_catalog_bytes, encode(catalog()))
        with self.assertRaises(w.BudgetStop): self.client.send(self.request)
        self.assertEqual(self.transport.call_count, 2)

    def test_invalid_then_valid_catalog_cannot_erase_failure(self):
        with self.assertRaises(n.NativePromptError): self.metadata({'models': []})
        with self.assertRaises(n.NativePromptError): self.metadata()
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.assertEqual(self.guard.posts, 0)

    def test_valid_then_http_failure_cannot_reuse_previous_capabilities(self):
        self.metadata()
        with self.assertRaises(n.NativePromptError): self.metadata(status=500)
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.assertEqual(self.guard.posts, 0)

    def test_transport_and_close_failure_revoke_capabilities_without_generation(self):
        for stream in (Stream([b'{'], error=TimeoutError()), Stream([encode(catalog())], close_error=OSError())):
            self.guard.reasoning_preflight_failed = False
            self.metadata()
            self.transport.return_value = stream
            req = types.SimpleNamespace(url=policy()['endpoint'].removesuffix('/v1')+'/api/v1/models',
                method='GET', content=b'', headers={}, extensions={})
            with self.assertRaises((TimeoutError, OSError)): self.client.send(req)
            with self.assertRaises(n.NativePromptError): self.client.send(self.request)
            self.assertEqual(self.guard.posts, 0)

    def test_translated_wire_is_blocked_after_valid_capabilities(self):
        self.metadata()
        self.request.content = encode(dict(binary_wire(self.guard.policy), reasoning_effort='none'))
        with self.assertRaisesRegex(n.NativePromptError, 'wire_parameters'): self.client.send(self.request)
        self.assertEqual((self.guard.posts, self.transport.call_count), (0, 1))


class BinaryTerminalTests(unittest.TestCase):
    def trace(self, mode='off', reasoning=False):
        p = binary_policy(mode); req = encode(request()); resp = response()
        if reasoning: resp['choices'][0]['message']['reasoning_content'] = 'synthetic reasoning'
        trace = dict(request_sha256=digest(req), uid=p['worker_uid'], gid=p['worker_gid'],
            identity_isolated=True, agent_class='AIAgent', runtime_policy_sha256=digest(encode(p)),
            bridge_sha256=p['bridge_sha256'], profile_passed=True, server_finished=True,
            native_completed=True, content_matches_wire=True, actual_requests=1, denied_continuations=0,
            wire_request_base64=base64.b64encode(encode(binary_wire(p))).decode(),
            wire_response_base64=base64.b64encode(encode(resp)).decode(),
            reasoning_catalog_base64=base64.b64encode(encode(catalog())).decode(),
            reasoning_preflight_passed=True)
        return p, req, trace

    def verify(self, p, req, trace):
        return n.verify_worker_terminal(encode(trace), req, 'c'*64, p, digest(encode(p)), 0)

    def test_both_modes_have_rechecked_request_and_capability_evidence(self):
        for mode in ('off', 'on'):
            p, req, trace = self.trace(mode, reasoning=mode == 'on')
            self.assertEqual(json.loads(self.verify(p, req, trace))['status'], 'completed')

    def test_off_with_returned_reasoning_is_rejected(self):
        with self.assertRaisesRegex(n.NativePromptError, 'reasoning_mismatch'):
            self.verify(*self.trace(reasoning=True))

    def test_missing_tampered_or_unverified_catalog_cannot_certify_terminal(self):
        for field, value in (('reasoning_catalog_base64', ''), ('reasoning_catalog_base64', base64.b64encode(encode({'models': []})).decode()),
                             ('reasoning_preflight_passed', False), ('reasoning_preflight_passed', 1)):
            p, req, trace = self.trace(); trace[field] = value
            with self.assertRaises(n.NativePromptError): self.verify(p, req, trace)


class BinarySelectorTests(unittest.TestCase):
    def fixture(self):
        from test_prospective_reasoning_budget import BudgetPlannerTests
        f = BudgetPlannerTests(); f.setUp()
        f.policies = {'none': binary_policy('off'), 'medium': binary_policy('on')}
        return f

    def arguments(self, f, version='hermes-reasoning-profiles/1.1'):
        args = f.arguments(); profiles = json.loads(args['profiles_bytes']); profiles['schema_version'] = version
        args['profiles_bytes'] = encode(profiles); args['profiles_sha256'] = digest(args['profiles_bytes'])
        return args

    def test_versioned_selector_retains_labels_but_records_actual_binary_mode_and_budget(self):
        f = self.fixture(); args = self.arguments(f); result = json.loads(f.m.build_plan(**args))
        self.assertEqual(result['schema_version'], 'hermes-reasoning-budget-plan/1.1')
        self.assertEqual([(e['profile'], e['reasoning_mode']) for e in result['entries']], [('none', 'off'), ('medium', 'on')])
        self.assertEqual(result['reserved_generation_tokens'], 3*512)
        with self.assertRaisesRegex(ValueError, 'generation_ceiling_exceeded'):
            f.m.build_plan(**dict(args, token_ceiling=3*512-1))

    def test_legacy_library_cannot_silently_relabel_binary_policies(self):
        f = self.fixture()
        with self.assertRaises(ValueError): f.m.build_plan(**self.arguments(f, 'hermes-reasoning-profiles/1.0'))

    def test_modes_and_nonfactor_model_binding_cannot_be_substituted(self):
        for key, value in (('reasoning_effort', 'off'), ('reasoning_model_sha256', 'f'*64), ('context_length', 4096)):
            f = self.fixture(); f.policies['medium'][key] = value
            with self.assertRaises(ValueError): f.m.build_plan(**self.arguments(f))


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged Linux evidence boundary')
class BinaryHostTests(unittest.TestCase):
    def test_binary_terminal_supports_host_recovery_without_resend_then_owned_unload(self):
        import test_native_tool_evidence as fixtures
        fixture = fixtures.ToolEvidenceTests()
        self.addCleanup(fixture.doCleanups)
        with patch.object(fixtures, 'tool_policy', binary_policy): fixture.setUp()
        fixture.trace_change = lambda trace: trace.update(
            reasoning_catalog_base64=base64.b64encode(encode(catalog())).decode(), reasoning_preflight_passed=True)
        fixture.test_verified_native_artifact_and_ownership_recover_host_without_resend()
        fixture.lease.finish()
        self.assertEqual(fixture.instances, [])
        self.assertEqual(fixture.sends, 1)

    def test_host_adapter_rechecks_committed_model_before_worker_start(self):
        from test_native_shadow_adapter import NativeLifecycleTests
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            fixture = NativeLifecycleTests()
            with patch('test_native_shadow_adapter.policy', binary_policy):
                adapter = fixture.adapter(directory)
            adapter._http = lambda *args: catalog()
            self.assertEqual(adapter._catalog(), {})
            changed = catalog(); changed['models'][0]['selected_variant'] = 'replacement'
            adapter._http = lambda *args: changed
            with self.assertRaisesRegex(n.NativePromptError, 'reasoning_capability_unverified'):
                adapter._catalog()


if __name__ == '__main__':
    unittest.main()
