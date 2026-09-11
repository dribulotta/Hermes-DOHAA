"""Offline scope validation for the reviewed one-proposal tool-route pipeline.

This validates a prospective source-bound plan, not execution, effect size,
causal superiority, host state, or effective internal model reasoning.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


_ROOT = Path(__file__).resolve().parents[1]
AUDITED_FILES = (
    'src/hermes_dohaa/assurance/gates.py',
    'src/hermes_dohaa/assurance/result_spec.py',
    'src/hermes_dohaa/assurance/semantic_assertions.py',
    'src/hermes_dohaa/contracts/models.py',
    'src/hermes_dohaa/controller/engine.py',
    'src/hermes_dohaa/controller/identity.py',
    'src/hermes_dohaa/controller/repair_policy.py',
    'src/hermes_dohaa/controller/semantic_repair.py',
    'src/hermes_dohaa/evidence/ledger.py',
    'src/hermes_dohaa/learning/native_prompt.py',
    'src/hermes_dohaa/learning/native_tool_contract.py',
    'src/hermes_dohaa/learning/shadow.py',
    'src/hermes_dohaa/runtime/base.py',
    'tools/controlled_tool_proposal.py',
    'tools/controlled_tool_routes.py',
    'tools/controlled_tools.py',
    'tools/host_steps.py',
    'tools/native_model_residency.py',
    'tools/native_tool_evidence.py',
)
# Reviewed at PR111. Updating this commitment requires a new source review;
# deriving the expected value automatically at validation time would defeat it.
AUDITED_SOURCE_SHA256 = 'f257124425ec1593a12b1764b5bad99591637d2964bb0a036892191c04378aec'
# Separately reviewed composition of PR119 with core fixes46/69,47 and62.
# The new evidence-policy dependency is part of this profile's commitment.
# Preserve the original profile; never accept a new checkout under its label.
INTEGRATED_AUDITED_FILES = tuple(sorted((*AUDITED_FILES,
    'src/hermes_dohaa/assurance/evidence_policy.py')))
INTEGRATED_AUDITED_SOURCE_SHA256 = '2f3488dda8397ea7a414a1e187946929444278dda3beb974951472be682ebe8b'
# Reviewed semantic boundary fixes: date overflow and ASCII-only array indices.
# This remains the same downstream one-proposal pipeline, with a new source pin.
BOUNDED_AUDITED_SOURCE_SHA256 = 'eefba9e31150c287ef278c2ec9fca75895236017b093edbe309d222132657c5e'
# Reviewed string-type admission for the five semantic-language selectors.
ADMISSION_AUDITED_SOURCE_SHA256 = '014d12c68af677689f039f7ce8fbed254c3150e7fba31e00bdb34b3022924a58'
_PROFILES = {
    'verified-tool-admission/1.0': (AUDITED_FILES, AUDITED_SOURCE_SHA256),
    'verified-tool-admission/1.1': (INTEGRATED_AUDITED_FILES, INTEGRATED_AUDITED_SOURCE_SHA256),
    'verified-tool-admission/1.2': (INTEGRATED_AUDITED_FILES, BOUNDED_AUDITED_SOURCE_SHA256),
    'verified-tool-admission/1.3': (INTEGRATED_AUDITED_FILES, ADMISSION_AUDITED_SOURCE_SHA256),
}
_PROTOCOL_FIELDS = frozenset(('schema_version', 'pipeline', 'outcome', 'study_kind', 'pairing', 'arms'))
_COMMITMENTS = ('input_sha256', 'proposal_sha256', 'initial_state_sha256',
                'permissions_sha256', 'policy_sha256', 'fault_schedule_sha256')
_ARM_FIELDS = frozenset((*_COMMITMENTS, 'route', 'max_native_calls', 'max_output_tokens', 'reasoning_strategy'))
_LIMIT = 65536


class ProtocolError(ValueError):
    """Stable bounded diagnostics; never include protocol values or file paths."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _hex(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _source_fingerprint(files=AUDITED_FILES):
    sources = {}
    total = 0
    try:
        for relative in files:
            path = _ROOT/relative
            if path.is_symlink() or not path.is_file():
                raise ProtocolError('audited_source_unavailable')
            # Fixed trusted checkout paths only; a protocol cannot provide paths.
            if any(parent.is_symlink() for parent in path.parents if parent != _ROOT and _ROOT in parent.parents):
                raise ProtocolError('audited_source_unavailable')
            with path.open('rb') as handle:
                data = handle.read(2*1024*1024 + 1)
            total += len(data)
            if len(data) > 2*1024*1024 or total > 12*1024*1024:
                raise ProtocolError('audited_source_unavailable')
            sources[relative] = _digest(data.replace(b'\r\n', b'\n'))
    except OSError as error:
        raise ProtocolError('audited_source_unavailable') from error
    return _digest(json.dumps(sources, sort_keys=True, separators=(',', ':')).encode())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('ambiguous_protocol_json')
        result[key] = value
    return result


def _nonfinite(value):
    raise ProtocolError('invalid_protocol_json')


def validate_protocol(data, expected_sha256):
    """Check committed JSON bytes against the built-in reviewed source profile.

    Arm commitments are planned equality constraints, not verified execution
    receipts. No transport, controller, store, or evaluator is invoked here.
    """
    if type(data) is not bytes or not 0 < len(data) <= _LIMIT:
        raise ProtocolError('protocol_size')
    if not _hex(expected_sha256) or _digest(data) != expected_sha256:
        raise ProtocolError('protocol_digest')
    try:
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_object, parse_constant=_nonfinite)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError('invalid_protocol_json') from error
    if type(raw) is not dict or set(raw) != _PROTOCOL_FIELDS:
        raise ProtocolError('protocol_fields')
    if raw['schema_version'] != 'hermes-route-attribution-protocol/1.0':
        raise ProtocolError('protocol_schema')
    if type(raw['pipeline']) is not str or raw['pipeline'] not in _PROFILES:
        raise ProtocolError('unsupported_pipeline')
    if raw['study_kind'] != 'fresh_synthetic_conformance':
        raise ProtocolError('unsupported_study_kind')
    if raw['pairing'] != 'identical_proposal_independent_state':
        raise ProtocolError('unsupported_pairing')
    # These facts belong to the reviewed code profile, never the caller's labels.
    files, expected_source = _PROFILES[raw['pipeline']]
    source_sha256 = _source_fingerprint(files)
    if source_sha256 != expected_source:
        raise ProtocolError('audited_source_changed')
    if raw['outcome'] == 'native_answer_quality':
        raise ProtocolError('outcome_precedes_intervention')
    if raw['outcome'] not in ('proposal_admission', 'effect_outcome', 'recovery_outcome'):
        raise ProtocolError('unsupported_outcome')
    arms = raw['arms']
    if type(arms) is not list or len(arms) != 2:
        raise ProtocolError('two_routes_required')
    for arm in arms:
        if type(arm) is not dict or set(arm) != _ARM_FIELDS:
            raise ProtocolError('arm_fields')
        if arm['route'] not in ('dohaa', 'simple') or any(not _hex(arm[k]) for k in _COMMITMENTS):
            raise ProtocolError('arm_commitment')
    if sorted(arm['route'] for arm in arms) != ['dohaa', 'simple']:
        raise ProtocolError('two_routes_required')
    shared = [{k: v for k, v in arm.items() if k != 'route'} for arm in arms]
    if shared[0] != shared[1]:
        raise ProtocolError('unequal_arm_conditions')
    for arm in arms:
        if type(arm['max_native_calls']) is not int or arm['max_native_calls'] != 1:
            raise ProtocolError('one_proposal_required')
        if type(arm['max_output_tokens']) is not int or not 1 <= arm['max_output_tokens'] <= _LIMIT:
            raise ProtocolError('invalid_token_limit')
        if arm['reasoning_strategy'] not in ('none', 'medium_default_on'):
            raise ProtocolError('unsupported_reasoning_strategy')
    return dict(schema_version='hermes-route-attribution-report/1.0', status='scope_validated',
                protocol_sha256=expected_sha256, source_sha256=source_sha256,
                pipeline=raw['pipeline'], outcome=raw['outcome'], routes=['dohaa', 'simple'],
                intervention_stage='after_native_terminal', quality_claim_supported=False,
                runtime_attested=False, execution_authorized=False, native_requests=0,
                independent_llm_observations=0, effective_mode_attested=False,
                on_depends_on_declared_default=arms[0]['reasoning_strategy'] == 'medium_default_on')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True, help='independently retained protocol digest')
    args = parser.parse_args()
    try:
        report = validate_protocol(sys.stdin.buffer.read(_LIMIT + 1), args.sha256)
    except ProtocolError as error:
        print(json.dumps(dict(status='rejected', reason=str(error))))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
