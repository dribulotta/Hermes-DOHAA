"""Full model context stays paired while worker homes remain independent."""
import base64
import copy
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning import native_context as context, native_prompt as n, shadow
from test_native_reasoning_compatibility import compat_policy
import test_native_reasoning_compatibility as compatibility
from test_native_binary_reasoning import binary_wire
import test_native_binary_reasoning as binary
import test_native_failure_diagnostics as diagnostics
from test_native_shadow_adapter import encode, digest, request, response


def fixed_policy(mode='off'):
    return dict(compat_policy(mode), schema_version=context.POLICY_VERSION,
                context_contract=context.CONTRACT)


def fixed_wire(p):
    raw = binary_wire(p)
    raw['messages'] = context.expected_messages(request(), n.prompt_frame(request()))
    return raw


class FixedContextTests(unittest.TestCase):
    def test_separate_homes_and_stale_caches_do_not_change_model_context(self):
        class Agent:
            def _build_system_prompt(self, system_message=None):
                return str(self.home)
        original = Agent._build_system_prompt
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            outputs = []
            for home in (first, second):
                a = Agent(); a.home = Path(home)
                a._cached_system_prompt = 'uncommitted ' + home
                a._cached_system_prompt_static = home
                context.configure_native_context(a, fixed_policy())
                self.assertEqual(a.home, Path(home))
                self.assertTrue(a.home.is_dir())
                self.assertIsNone(a._cached_system_prompt)
                self.assertIsNone(a._cached_system_prompt_static)
                # Native conversation startup rebuilds this; setting cache alone fails.
                outputs.append(a._build_system_prompt())
                self.assertEqual(a._build_system_prompt_parts(), (context.BASE_SYSTEM, '', ''))
                with self.assertRaises(ValueError): a._build_system_prompt('override')
            self.assertNotEqual(first, second)
            self.assertEqual(outputs, [context.BASE_SYSTEM]*2)
        self.assertIs(Agent._build_system_prompt, original)

    def test_new_contract_is_required_without_implicit_legacy_upgrade(self):
        for mode in ('off', 'on'):
            p = fixed_policy(mode)
            self.assertEqual(n.validate_native_policy(encode(p), digest(encode(p))), p)
            self.assertEqual(n.validate_wire_request(encode(fixed_wire(p)), request(), p), fixed_wire(p))
        for value in (None, '', 'fixed-native-json-context/2.0', True):
            p = dict(fixed_policy(), context_contract=value)
            with self.assertRaises(n.NativePromptError): n.validate_native_policy(encode(p), digest(encode(p)))
        p = fixed_policy(); del p['context_contract']
        with self.assertRaises(shadow.ShadowError): n.validate_native_policy(encode(p), digest(encode(p)))
        p = dict(compat_policy(), context_contract=context.CONTRACT)
        with self.assertRaises(shadow.ShadowError): n.validate_native_policy(encode(p), digest(encode(p)))
        a = types.SimpleNamespace(_cached_system_prompt='legacy')
        context.configure_native_context(a, compat_policy())
        self.assertEqual(vars(a), {'_cached_system_prompt': 'legacy'})
        p = compat_policy()
        n.validate_wire_request(encode(binary_wire(p)), request(), p)

    def test_all_context_changes_rejected_even_when_task_and_frame_match(self):
        p = fixed_policy(); original = fixed_wire(p)['messages']
        variants = []
        for suffix in ('\nHOME=/tmp/worker-other', '\nCurrent time: changed', '\nextra identity'):
            changed = copy.deepcopy(original); changed[0]['content'] += suffix; variants.append(changed)
        changed = copy.deepcopy(original); changed[0]['name'] = 'extra'; variants.append(changed)
        changed = copy.deepcopy(original); changed[1]['name'] = 'extra'; variants.append(changed)
        variants.extend([original[::-1], original+[{'role':'system','content':''}],
                         [dict(original[0], content=n.prompt_frame(request())), original[1]]])
        for messages in variants:
            with self.subTest(messages=messages), self.assertRaisesRegex(n.NativePromptError, 'wire_messages'):
                n.validate_wire_request(encode(dict(fixed_wire(p), messages=messages)), request(), p)

    def test_terminal_rechecks_original_context_bytes(self):
        for mode in ('off', 'on'):
            f, _, req, trace = compatibility.CompatibilityTerminalTests().fixture(mode)
            p = fixed_policy(mode); raw = fixed_wire(p)
            trace.update(runtime_policy_sha256=digest(encode(p)),
                         wire_request_base64=base64.b64encode(encode(raw)).decode())
            self.assertEqual(json.loads(f.verify(p, req, trace))['status'], 'completed')
            raw['messages'][0]['content'] += '\n/temporary/uncommitted'
            trace['wire_request_base64'] = base64.b64encode(encode(raw)).decode()
            with self.assertRaisesRegex(n.NativePromptError, 'wire_messages'): f.verify(p, req, trace)

    def test_planner_requires_new_library_and_preserves_budget(self):
        f = compatibility.CompatibilitySelectorTests().fixture()
        f.policies = {'none': fixed_policy('off'), 'medium': fixed_policy('on')}
        args = f.arguments(); library = json.loads(args['profiles_bytes'])
        for version in ('1.0', '1.1', '1.2', '1.3'):
            library['schema_version'] = 'hermes-reasoning-profiles/' + version
            args.update(profiles_bytes=encode(library), profiles_sha256=digest(encode(library)))
            if version != '1.3':
                with self.assertRaisesRegex(ValueError, 'profile_mode_version_mismatch'): f.m.build_plan(**args)
            else:
                plan = json.loads(f.m.build_plan(**args))
                self.assertEqual(plan['schema_version'], 'hermes-reasoning-budget-plan/1.3')
                self.assertEqual(plan['reserved_generation_tokens'], 3*512)
        f.policies['medium'] = compat_policy('on')
        args = f.arguments(); library = json.loads(args['profiles_bytes'])
        library['schema_version'] = 'hermes-reasoning-profiles/1.3'
        args.update(profiles_bytes=encode(library), profiles_sha256=digest(encode(library)))
        with self.assertRaisesRegex(ValueError, 'profile_mode_version_mismatch'): f.m.build_plan(**args)


class FixedContextTransportTests(unittest.TestCase):
    def setUp(self):
        diagnostics.GuardDiagnosticsTests.setUp(self)
        self.guard.policy = fixed_policy()

    def test_extra_environment_is_blocked_before_generation_sender(self):
        binary.BinaryGuardTests.metadata(self)
        self.transport.reset_mock()
        raw = fixed_wire(self.guard.policy)
        raw['messages'][0]['content'] += '\nCWD=/tmp/uncommitted'
        self.request.content = encode(raw)
        with self.assertRaisesRegex(n.NativePromptError, 'wire_messages'): self.client.send(self.request)
        self.transport.assert_not_called()
        self.assertEqual(self.guard.posts, 0)

    def test_fixed_context_still_requires_catalog_and_single_generation(self):
        self.request.content = encode(fixed_wire(self.guard.policy))
        with self.assertRaises(n.NativePromptError): self.client.send(self.request)
        self.transport.assert_not_called()
        binary.BinaryGuardTests.metadata(self)
        self.transport.return_value = diagnostics.Stream([encode(response())])
        self.client.send(self.request)
        self.assertEqual(self.guard.posts, 1)
        with self.assertRaises(binary.w.BudgetStop): self.client.send(self.request)
        self.assertEqual(self.transport.call_count, 2)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'privileged Linux evidence boundary')
class FixedContextHostTests(unittest.TestCase):
    def test_fixed_profiles_preserve_verified_recovery_and_exact_unload(self):
        import test_native_tool_evidence as fixtures
        from hermes_dohaa.learning.native_reasoning import reasoning_request_evidence
        for mode in ('off', 'on'):
            fixture = fixtures.ToolEvidenceTests(); self.addCleanup(fixture.doCleanups)
            with patch.object(fixtures, 'tool_policy', lambda: fixed_policy(mode)): fixture.setUp()
            def context_trace(trace):
                raw = json.loads(base64.b64decode(trace['wire_request_base64']))
                logical = fixture.logical
                raw['messages'] = context.expected_messages(logical, n.prompt_frame(logical))
                trace.update(wire_request_base64=base64.b64encode(encode(raw)).decode(),
                    reasoning_catalog_base64=base64.b64encode(encode(binary.catalog())).decode(),
                    reasoning_preflight_passed=True, reasoning_request_evidence=reasoning_request_evidence(fixture.p))
            fixture.trace_change = context_trace
            fixture.test_verified_native_artifact_and_ownership_recover_host_without_resend()
            fixture.lease.finish()
            self.assertEqual(fixture.instances, [])
            self.assertEqual(fixture.sends, 1)


if __name__ == '__main__':
    unittest.main()
