"""Bounded packing of full decision dependencies for structured records.

Both policies read the same full input and direct resolver. This is a pure
development helper, not a native-model integration or a world-truth check.
"""
import hashlib
import json

from tools.document_stream_projection import current_document_view
from tools.document_stream_request import canonical
from tools.evidence_records import resolve_records


POLICIES = ('complete_coverage', 'complete_recency')


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def select_evidence(request, query_contract, *, policy, max_bytes):
    """Pack whole active revisions with an exact request-plus-contract byte cap.

    Citation alternatives are not decision alternatives: a resolved query needs
    every nominated root and every dependency used to check all matching roots.
    Global malformed-input status cannot be escaped by discarding a source.
    The receipt retains original unresolved queries outside the bounded payload.
    Always call with the full delivery log, never a prior selected snapshot.
    """
    if type(policy) is not str or policy not in POLICIES:
        raise ValueError('unsupported evidence selection policy')
    if type(max_bytes) is not int or not 0 <= max_bytes <= 131072:
        raise ValueError('invalid envelope byte budget')
    original = resolve_records(request, query_contract)
    active, _ = current_document_view(request)
    events = active['observation']['events']
    by_source = {event['source_id']: event for event in events}
    roots = {query['key']: query['root_sources'] for query in query_contract['queries']}
    groups = []
    for row in original['queries']:
        if row['status'] != 'resolved':
            continue
        sources = set(roots[row['key']])
        for group in row['groups']:
            sources.update(item['source_id'] for item in group['evidence'])
        groups.append(dict(key=row['key'], source_ids=sorted(sources),
            evidence=[dict(source_id=source, revision=by_source[source]['revision']) for source in sorted(sources)]))

    def payload(selected):
        view = dict(active, observation=dict(active['observation'],
                    events=[event for event in events if event['source_id'] in selected]))
        return dict(request=view, query_contract=query_contract)

    def byte_cost(selected):
        return len(canonical(payload(selected)))

    required = {group['key']: frozenset(group['source_ids']) for group in groups}
    sources = sorted(set().union(*required.values())) if required else []
    # The resolver validates the <=12 permitted-source contract before this
    # bound. Keep a local guard so a future upstream expansion cannot silently
    # make this exact enumeration unbounded.
    if len(sources) > 12:
        raise ValueError('exact selection source limit')

    def covered(selected):
        return [key for key, dependency in required.items() if dependency <= selected]

    minimum_bytes = byte_cost(set())
    status = ('out_of_scope' if original['status'] == 'out_of_scope' else
              'budget_exceeded' if minimum_bytes > max_bytes else 'selected')
    selected = frozenset()
    if status == 'selected' and policy == 'complete_coverage':
        best = (0, minimum_bytes, ())
        for mask in range(1, 1 << len(sources)):
            candidate = frozenset(source for index, source in enumerate(sources) if mask & (1 << index))
            cost = byte_cost(candidate)
            if cost > max_bytes:
                continue
            rank = (-len(covered(candidate)), cost, tuple(sorted(candidate)))
            if rank < best:
                selected, best = candidate, rank
    elif status == 'selected':
        # Prefer the latest completing delivery of an entire group, then its
        # oldest component, then the stable key. This comparator uses the same
        # complete dependencies and cost as exact coverage, with no fragments.
        def recency(key):
            ticks = [by_source[source]['arrived_at'] for source in required[key]]
            return -max(ticks), -min(ticks), key
        for key in sorted(required, key=recency):
            candidate = selected | required[key]
            if byte_cost(candidate) <= max_bytes:
                selected = candidate

    bounded_payload = payload(selected) if status == 'selected' else None
    covered_keys = covered(selected) if bounded_payload is not None else []
    if bounded_payload is not None:
        # Check that removal did not change an answer, erase a dependency used
        # to establish it, or promote an originally unresolved query to a fact.
        after = resolve_records(bounded_payload['request'], query_contract)
        before_rows = {row['key']: row for row in original['queries']}
        after_rows = {row['key']: row for row in after['queries']}
        if ([fact['key'] for fact in after['facts']] != covered_keys or
                any(canonical(before_rows[key]) != canonical(after_rows[key]) for key in covered_keys)):
            raise RuntimeError('selection changed original resolution requirements')
        if len(canonical(bounded_payload)) > max_bytes:
            raise RuntimeError('selection byte accounting mismatch')

    omitted = []
    for row in original['queries']:
        if row['key'] not in covered_keys:
            omitted.append(dict(key=row['key'], original_status=row['status'],
                                reasons=row['reasons'],
                                reason='budget' if row['status'] == 'resolved' else 'original_not_resolved'))
    result = dict(schema_version='hermes-evidence-selection/1.0', policy=policy,
        status=status, budget_bytes=max_bytes, minimum_bytes=minimum_bytes,
        input_sha256=original['input_sha256'], contract_sha256=original['contract_sha256'],
        active_sha256=original['active_sha256'], input_envelope_bytes=len(canonical(dict(request=request, query_contract=query_contract))),
        payload=bounded_payload, output_bytes=len(canonical(bounded_payload)) if bounded_payload is not None else None,
        output_sha256=_hash(bounded_payload) if bounded_payload is not None else None,
        selected_source_ids=sorted(selected), decision_groups=groups, covered_keys=covered_keys,
        omitted_queries=omitted, original_resolution=original,
        complete=len(covered_keys) == len(active['task']['fact_keys']),
        native_requests=0, semantic_truth_verified=False)
    return json.loads(canonical(result))


def verify_selection(original, query_contract, result, *, policy, max_bytes):
    """Recompute from full input; hashes and receipts do not authenticate truth."""
    try:
        expected = select_evidence(original, query_contract, policy=policy, max_bytes=max_bytes)
        return canonical(expected) == canonical(result)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return False
