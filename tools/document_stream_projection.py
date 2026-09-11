"""Disposable current-source views of validated development observations.

Always rebuild from the complete delivery log, never from an earlier projection.
The receipt binds bytes and the selection rule; it does not authenticate sources
or establish truth. This module reads no grader or model and stores no history.
"""
import hashlib

from tools.document_stream import DocumentEvent
from tools.document_stream_request import canonical, make_request, validate_request


def current_document_view(request: dict) -> tuple[dict, dict]:
    """Retain one delivery per active latest revision, including irrelevant ones.

Validate all original deliveries before dropping any. Retraction/expiry are
applied to the latest revision, so an older active revision cannot revive.
Preserve delivery order and task instructions. Return fresh, unaliased objects.
"""
    original = validate_request(request)
    observation = original['observation']
    events = [DocumentEvent(**raw) for raw in observation['events']]
    latest = {}
    for event in events:
        if event.source_id not in latest or event.revision > latest[event.source_id].revision:
            latest[event.source_id] = event
    removed = {'superseded': 0, 'duplicate_latest': 0, 'expired': 0, 'retracted': 0}
    retained = []
    for event in events:
        current = latest[event.source_id]
        if event.revision < current.revision:
            removed['superseded'] += 1
        elif event.event_id != current.event_id:
            removed['duplicate_latest'] += 1
        elif event.retracted:
            removed['retracted'] += 1
        elif event.expires_at is not None and observation['at_tick'] >= event.expires_at:
            removed['expired'] += 1
        else:
            retained.append(event.__dict__)
    projected = make_request({'run_id': observation['run_id'], 'at_tick': observation['at_tick'],
                              'events': retained}, original['task'])
    before, after = canonical(original), canonical(projected)
    receipt = {'schema_version': 'hermes-current-document-view/1.0',
               'input_sha256': hashlib.sha256(before).hexdigest(),
               'output_sha256': hashlib.sha256(after).hexdigest(),
               'input_bytes': len(before), 'output_bytes': len(after),
               'bytes_saved': len(before) - len(after),
               'input_events': len(events), 'output_events': len(retained),
               'sources_seen': len(latest), 'removed_events': removed}
    return projected, receipt


def verify_current_view(original: dict, projected: dict, receipt: dict) -> bool:
    """Recompute exact content and counts; hashes alone are not a trusted proof."""
    try:
        expected, expected_receipt = current_document_view(original)
        # Compare canonical bytes to distinguish booleans from integer counts.
        return canonical(projected) == canonical(expected) and canonical(receipt) == canonical(expected_receipt)
    except (ValueError, TypeError, KeyError, RecursionError):
        return False
