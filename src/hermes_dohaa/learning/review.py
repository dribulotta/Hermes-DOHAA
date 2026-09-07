"""Append independently reviewed development dispositions; no activation authority."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import shadow
from .collection import CollectionError, audit_prompt_collection

_ZERO = '0' * 64
_DECISIONS = {'recommend_for_development', 'reject', 'revoke'}


def _bindings(report):
    return {'collection_sha256': shadow._hash(shadow._canonical(report)),
            'recording_sha256': report['recording_sha256'],
            'result_id': report['result']['result_id'],
            'candidate_id': report['result']['candidate_id']}


def _validate(record, index, previous, bindings):
    shadow._fields(record, {'schema_version', 'sequence', 'previous_sha256', 'decision',
        'reviewer_sha256', 'reason_sha256', 'candidate_state', 'activation_authorized', *bindings})
    if (record['schema_version'] != 'hermes-shadow-review/1.0'
            or type(record['sequence']) is not int or record['sequence'] != index
            or record['previous_sha256'] != previous
            or record['candidate_state'] != 'quarantined'
            or record['activation_authorized'] is not False
            or any(record[key] != value for key, value in bindings.items())
            or type(record['decision']) is not str or record['decision'] not in _DECISIONS):
        raise CollectionError('review.record_invalid')
    shadow._digest(record['reviewer_sha256'])
    shadow._digest(record['reason_sha256'])


def read_collection_reviews(directory: Path, *, collection_report: dict[str, Any],
                            expected_head_sha256: str) -> dict[str, Any]:
    """Read a local, operator-controlled review chain against an external head pin.

    The collection report must come from audit_prompt_collection. This helper
    alone is not an execution audit, identity check or authorization decision.
    """
    shadow._digest(expected_head_sha256)
    bindings = _bindings(collection_report)
    previous, decision, count = _ZERO, 'unreviewed', 0
    for index in range(2):
        path = directory / f'review-{index}.json'
        if not path.exists() and not path.is_symlink():
            break
        if path.is_symlink() or not path.is_file():
            raise CollectionError('review.file_invalid')
        data = shadow._read(path)
        record = shadow._json(data, shadow._hash(data))
        _validate(record, index, previous, bindings)
        if ((index == 0 and record['decision'] == 'revoke')
                or (index == 1 and (decision != 'recommend_for_development' or record['decision'] != 'revoke'))):
            raise CollectionError('review.transition_invalid')
        if (record['decision'] == 'recommend_for_development'
                and collection_report['result']['verdict'] != 'meets_predeclared_criteria'):
            raise CollectionError('review.negative_result')
        previous, decision, count = shadow._hash(data), record['decision'], index + 1
    # Reject a missing predecessor or unexpected continuation, not just a valid prefix.
    if {p.name for p in directory.glob('review-*.json')} != {f'review-{i}.json' for i in range(count)}:
        raise CollectionError('review.chain_invalid')
    if previous != expected_head_sha256:
        raise CollectionError('review.head_mismatch')
    return {'decision': decision, 'count': count, 'head_sha256': previous,
            **bindings, 'candidate_state': 'quarantined', 'activation_authorized': False}


def record_collection_review(
    directory: Path, *, audit_arguments: dict[str, Any], expected_collection_sha256: str,
    expected_previous_sha256: str, decision: str, reviewer_sha256: str, reason_sha256: str,
) -> dict[str, Any]:
    """Audit exact bytes, then exclusively append a recommendation/rejection/revocation.

    Directory must already exist and be writable only by authorized reviewers.
    Reviewer/reason commitments are references to independently retained private
    authorization records, not signatures or proof that approval was granted.
    A recommendation is ONLY for development review; it never changes a prompt,
    applies a patch, grants deployment permission, or deletes original evidence.
    """
    if os.name != 'posix':
        raise CollectionError('review.platform_unsupported')
    for digest in (expected_collection_sha256, expected_previous_sha256, reviewer_sha256, reason_sha256):
        shadow._digest(digest)
    if type(decision) is not str or decision not in _DECISIONS:
        raise CollectionError('review.decision_invalid')
    report = audit_prompt_collection(**audit_arguments)
    if shadow._hash(shadow._canonical(report)) != expected_collection_sha256:
        raise CollectionError('review.collection_mismatch')
    state = read_collection_reviews(directory, collection_report=report,
                                    expected_head_sha256=expected_previous_sha256)
    if (state['decision'] in {'reject', 'revoke'}
            or (state['decision'] == 'unreviewed' and decision == 'revoke')
            or (state['decision'] == 'recommend_for_development' and decision != 'revoke')):
        raise CollectionError('review.transition_invalid')
    if decision == 'recommend_for_development' and report['result']['verdict'] != 'meets_predeclared_criteria':
        raise CollectionError('review.negative_result')
    record = {'schema_version': 'hermes-shadow-review/1.0', 'sequence': state['count'],
              'previous_sha256': expected_previous_sha256, 'decision': decision,
              'reviewer_sha256': reviewer_sha256, 'reason_sha256': reason_sha256,
              **_bindings(report), 'candidate_state': 'quarantined', 'activation_authorized': False}
    data = shadow._canonical(record)
    shadow._publish(directory / f'review-{state["count"]}.json', data)
    return {'decision': decision, 'head_sha256': shadow._hash(data),
            'candidate_state': 'quarantined', 'activation_authorized': False}
