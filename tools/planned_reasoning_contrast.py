"""Diagnose prospective profile assignments without executing a workload."""
from pathlib import Path

from hermes_dohaa.learning import native_prompt, shadow
from tools import prospective_reasoning_budget as budget


STRATEGIES = ('adaptive', 'fixed-none', 'fixed-medium')


class ContrastError(ValueError):
    """Bounded diagnostics without caller values or private plan contents."""


def diagnose_contrast(*, plans, workload_bytes, workload_sha256,
                      profiles_bytes, profiles_sha256, rules_bytes,
                      rules_sha256, token_ceiling):
    """Return canonical report bytes for three independently retained plans.

    Each plan is {document_bytes: bytes, document_sha256: str}. All strategies
    must recompute from ONE shared set of original committed build inputs.
    Request IDs describe counterfactual reservations, not three dispatch grants.
    The caller must independently retain trustworthy original commitments.
    """
    if type(plans) is not dict or set(plans) != set(STRATEGIES):
        raise ContrastError('three_strategies_required')
    originals = dict(workload_bytes=workload_bytes, workload_sha256=workload_sha256,
                     profiles_bytes=profiles_bytes, profiles_sha256=profiles_sha256,
                     rules_bytes=rules_bytes, rules_sha256=rules_sha256,
                     token_ceiling=token_ceiling)
    validated = {}
    plan_digests = {}
    for strategy in STRATEGIES:
        envelope = plans[strategy]
        if type(envelope) is not dict or set(envelope) != {'document_bytes', 'document_sha256'}:
            raise ContrastError('plan_envelope_invalid')
        try:
            validated[strategy] = budget.validate_plan(
                envelope['document_bytes'], envelope['document_sha256'],
                strategy=strategy, **originals)
        except (ValueError, TypeError, native_prompt.NativePromptError):
            raise ContrastError('plan_or_original_inputs_invalid') from None
        plan_digests[strategy] = envelope['document_sha256']

    if len({plan['source_sha256'] for plan in validated.values()}) != 1:
        raise ContrastError('planner_source_changed_during_validation')

    # Recomputed shared inputs guarantee the same ordered tasks, descriptors,
    # request opportunities, rules, library and nonfactor settings. Compare the
    # selected policy commitments, not strategy names or matched-rule labels.
    adaptive = validated['adaptive']
    comparisons = {}
    identical_to = []
    for strategy in STRATEGIES[1:]:
        fixed = validated[strategy]
        changed = sum(a['profile_sha256'] != b['profile_sha256']
                      for a, b in zip(adaptive['entries'], fixed['entries']))
        comparisons[strategy] = dict(
            changed_task_count=changed,
            unchanged_task_count=adaptive['task_count'] - changed,
            assignment_identical=changed == 0)
        if changed == 0:
            identical_to.append(strategy)

    return shadow._canonical(dict(
        schema_version='hermes-planned-reasoning-contrast/1.0',
        status='contrast_absent' if identical_to else 'contrast_present',
        comparison_scope='planned_profile_assignment_only',
        identical_to=identical_to, comparisons=comparisons,
        task_count=adaptive['task_count'],
        planned_requests_per_strategy=adaptive['request_count'],
        plan_sha256=plan_digests,
        workload_sha256=workload_sha256, profiles_sha256=profiles_sha256,
        rules_sha256=rules_sha256, token_ceiling=token_ceiling,
        planner_source_sha256=adaptive['source_sha256'],
        diagnostic_source_sha256=shadow._hash(
            Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        native_requests=0, execution_authorized=False, source_admitted=False,
        effective_mode_attested=False, quality_claim_supported=False,
        savings_measured=False))
