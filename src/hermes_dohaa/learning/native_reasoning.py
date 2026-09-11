"""Opt-in binary intent and bounded catalog evidence; no server attestation.

Only exact catalog keys are admitted. Defaults are never substituted silently:
the compatibility contract declares its dependency on a committed default-on.
No request is dispatched here and returned thought text is not mode attestation.
"""
from . import shadow

BINARY_POLICY_VERSION = 'hermes-native-tool-policy/1.1'
COMPATIBILITY_POLICY_VERSION = 'hermes-native-tool-policy/1.2'
COMPATIBILITY_CONTRACT = 'lmstudio-binary-via-generic-effort/1.0'
MAX_CATALOG_BYTES = 512 * 1024
MAX_MODELS = 256


def binary_reasoning(policy):
    return policy.get('schema_version') in (BINARY_POLICY_VERSION, COMPATIBILITY_POLICY_VERSION)


def compatibility_reasoning(policy):
    return policy.get('schema_version') == COMPATIBILITY_POLICY_VERSION


def reasoning_enabled(policy):
    if compatibility_reasoning(policy):
        mode = policy.get('reasoning_intent')
        if (type(mode) is not str or mode not in ('off', 'on')
                or policy.get('reasoning_contract') != COMPATIBILITY_CONTRACT
                or policy.get('reasoning_effort') != {'off': 'none', 'on': 'medium'}[mode]):
            raise ValueError('explicit_compatibility_reasoning_contract_required')
        return mode == 'on'
    if binary_reasoning(policy):
        if policy.get('reasoning_effort') not in ('off', 'on'):
            raise ValueError('binary_reasoning_mode_required')
        return policy['reasoning_effort'] == 'on'
    return policy['reasoning_effort'] != 'none'


def selector_profile(policy):
    if binary_reasoning(policy):
        return 'medium' if reasoning_enabled(policy) else 'none'
    return policy['reasoning_effort']


def reasoning_request_evidence(policy):
    """Describe requests, never infer the server's actual internal mode.

    Medium is a compatibility wire value. For this opt-in binary/default-on
    contract its unsupported-model-value fallback is acknowledged explicitly.
    A returned reasoning block does not upgrade this to effective attestation.
    """
    if not compatibility_reasoning(policy):
        raise ValueError('compatibility_policy_required')
    enabled = reasoning_enabled(policy)
    return {'schema_version': 'hermes-reasoning-request-evidence/1.0',
            'intent': policy['reasoning_intent'], 'wire_effort': policy['reasoning_effort'],
            'on_depends_on_declared_default': enabled, 'model_default': 'on',
            'effective_mode_attested': False}


def binary_model_sha256(data, model):
    """Bind the exact key, capabilities and variant, excluding only residency.

    A trusted host commits this digest before dispatch. Captured metadata can
    check that commitment, not authenticate the server or its internal setting.
    Changing even descriptive metadata requires a fresh commitment. Instances
    may load/unload; residency ownership is checked separately by the host.
    """
    if type(data) is not bytes or not 0 < len(data) <= MAX_CATALOG_BYTES:
        raise ValueError('bounded_reasoning_catalog_required')
    if type(model) is not str or not 1 <= len(model) <= 256:
        raise ValueError('exact_model_key_required')
    raw = shadow._json(data, shadow._hash(data))
    models = raw.get('models')
    if type(models) is not list or not 0 < len(models) <= MAX_MODELS:
        raise ValueError('bounded_model_catalog_required')
    seen, instances, selected = set(), set(), None
    for item in models:
        if type(item) is not dict or type(item.get('key')) is not str or not item['key'] or item['key'] in seen:
            raise ValueError('ambiguous_model_key')
        seen.add(item['key'])
        loaded = item.get('loaded_instances')
        if type(loaded) is not list or len(loaded) > MAX_MODELS:
            raise ValueError('bounded_model_instances_required')
        aliases = [item.get('id')]
        for instance in loaded:
            if (type(instance) is not dict or type(instance.get('id')) is not str
                    or not instance['id'] or instance['id'] in instances):
                raise ValueError('ambiguous_model_instance')
            instances.add(instance['id']); aliases.append(instance['id'])
        if item['key'] != model and model in aliases:
            raise ValueError('cross_model_alias_collision')
        if item['key'] == model:
            selected = item
    if selected is None or selected.get('type') not in ('llm', 'vlm'):
        raise ValueError('exact_model_key_required')
    capabilities = selected.get('capabilities')
    reasoning = capabilities.get('reasoning') if type(capabilities) is dict else None
    if type(reasoning) is not dict:
        raise ValueError('explicit_binary_capabilities_required')
    options = reasoning.get('allowed_options')
    if (type(options) is not list or len(options) != 2 or any(type(x) is not str for x in options)
            or set(options) != {'off', 'on'} or reasoning.get('default') not in ('off', 'on')):
        raise ValueError('explicit_binary_capabilities_required')
    return shadow._hash(shadow._canonical({k: v for k, v in selected.items() if k != 'loaded_instances'}))


def verify_binary_catalog(data, policy):
    if not binary_reasoning(policy):
        raise ValueError('binary_policy_required')
    reasoning_enabled(policy)
    expected = policy.get('reasoning_model_sha256')
    shadow._digest(expected)
    if binary_model_sha256(data, policy['model']) != expected:
        raise ValueError('reasoning_model_commitment_changed')
    if compatibility_reasoning(policy):
        catalog = shadow._json(data, shadow._hash(data))
        selected = next(item for item in catalog['models'] if item['key'] == policy['model'])
        if selected['capabilities']['reasoning']['default'] != 'on':
            raise ValueError('explicit_default_on_dependency_unsatisfied')
