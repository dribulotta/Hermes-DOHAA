"""Opt-in, source-bound model context independent of worker filesystem paths.

This is a deliberately minimal native comparison profile. It omits Hermes'
dynamic environment, identity, profile and clock hints; it does not change the
worker's HOME, cwd, UID or filesystem isolation. The outgoing request guard
separately enforces the entire message list, including ephemeral additions.
"""
from types import MethodType

POLICY_VERSION = 'hermes-native-tool-policy/1.3'
CONTRACT = 'fixed-native-json-context/1.0'
BASE_SYSTEM = 'Return only the requested final JSON object.'


def fixed_context(policy):
    return policy.get('schema_version') == POLICY_VERSION


def validate_context_contract(policy):
    if fixed_context(policy) and policy.get('context_contract') != CONTRACT:
        raise ValueError('explicit_fixed_context_contract_required')


def expected_messages(logical, frame):
    return [{'role': 'system', 'content': BASE_SYSTEM + '\n\n' + frame},
            {'role': 'user', 'content': logical['input']}]


def _build_parts(self, system_message=None):
    if system_message is not None:
        raise ValueError('fixed_context_system_override_denied')
    return BASE_SYSTEM, '', ''


def _build_prompt(self, system_message=None):
    return _build_parts(self, system_message)[0]


def configure_native_context(agent, policy):
    """Bind only this fresh AIAgent; a cache assignment alone is insufficient.

    The pinned native conversation loop rebuilds its prompt at session start.
    Replacing both instance builders also covers native static-prefix rebuilds.
    Any later hook/context injection still fails the independent wire check.
    """
    validate_context_contract(policy)
    if not fixed_context(policy):
        return
    agent._build_system_prompt_parts = MethodType(_build_parts, agent)
    agent._build_system_prompt = MethodType(_build_prompt, agent)
    agent._cached_system_prompt = None
    agent._cached_system_prompt_static = None
