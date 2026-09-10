"""Competing pre-generation selectors over the same active document memory.

Lexical coverage is not semantic sufficiency. Recompute from the full delivery
observation, never ingest new events into a previously selected snapshot.
"""
from fractions import Fraction
import hashlib
from pathlib import Path
import re
import unicodedata

from tools.document_stream_projection import current_document_view
from tools.document_stream_request import canonical, make_request


STRATEGIES = ('coverage_per_byte', 'per_key_recency')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def selector_sha256():
    root = Path(__file__).resolve().parent
    return _hash(canonical({name: _hash((root/name).read_bytes().replace(b'\r\n', b'\n'))
                           for name in ('query_memory.py', 'document_stream_projection.py',
                                        'document_stream_request.py', 'document_stream.py')}))


def _tokens(text):
    return frozenset(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text).casefold()))


def select_memory(request, *, strategy, max_bytes):
    """Select entire revisions within an absolute serialized-request byte cap.

    No grader, references, LLM, transport, store mutation, summarization, or
    authority inference. Both strategies inspect the same public input.
    """
    if strategy not in STRATEGIES:
        raise ValueError('unsupported memory strategy')
    if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
        raise ValueError('invalid serialized request budget')
    active, projection = current_document_view(request)
    events = active['observation']['events']
    empty = make_request(dict(active['observation'], events=[]), active['task'])
    used_bytes = len(canonical(empty))
    if used_bytes > max_bytes:
        raise ValueError('unchanged task header exceeds budget')
    keys = active['task']['fact_keys']
    query_tokens = {key: _tokens(key) for key in keys}
    document_tokens = [_tokens(event['text']) for event in events]
    matches = [{key for key, tokens in query_tokens.items() if tokens and tokens <= terms}
               for terms in document_tokens]
    sizes = [len(canonical(event)) for event in events]
    selected = set()
    covered = set()

    def recency(index):
        event = events[index]
        return (-event['arrived_at'], -event['revision'], event['source_id'], event['event_id'])

    order = sorted(range(len(events)), key=recency)

    def cost(index):
        return sizes[index] + bool(selected)  # Exact JSON array separator cost.

    def fits(index):
        return index not in selected and used_bytes + cost(index) <= max_bytes

    def add(index):
        nonlocal used_bytes
        used_bytes += cost(index)
        selected.add(index)
        covered.update(matches[index])

    if strategy == 'coverage_per_byte':
        while True:
            choices = [index for index in order if fits(index) and matches[index] - covered]
            if not choices:
                break
            def priority(index):
                gain = len(matches[index] - covered)
                return (-Fraction(gain, cost(index)), -gain, *recency(index))
            add(min(choices, key=priority))

    # Strong simple comparator and shared fill: one fitting newest match per key
    # per round. Overlap is retained as corroborating context when it fits.
    while True:
        changed = False
        for key in keys:
            index = next((i for i in order if key in matches[i] and fits(i)), None)
            if index is not None:
                add(index)
                changed = True
        if not changed:
            break
    # Identical no-match/remaining-space fallback for both strategies.
    for index in order:
        if fits(index):
            add(index)
    retained = [event for index, event in enumerate(events) if index in selected]
    view = make_request(dict(active['observation'], events=retained), active['task'])
    output = canonical(view)
    if len(output) != used_bytes or used_bytes > max_bytes:
        raise RuntimeError('serialized selection accounting mismatch')
    receipt = dict(schema_version='hermes-query-memory-selection/1.0', strategy=strategy,
                   selector_sha256=selector_sha256(), budget_bytes=max_bytes,
                   input_sha256=projection['input_sha256'], input_bytes=projection['input_bytes'],
                   active_sha256=_hash(canonical(active)), active_bytes=len(canonical(active)),
                   output_sha256=_hash(output), output_bytes=len(output),
                   active_events=len(events), selected_events=len(retained),
                   omitted_active_events=len(events)-len(retained),
                   selected_event_ids=[event['event_id'] for event in retained],
                   lexically_covered_keys=[key for key in keys if key in covered],
                   semantic_sufficiency_verified=False, native_requests=0)
    return view, receipt


def verify_selection(original, selected, receipt, *, strategy, max_bytes):
    """Recompute from the original; receipts are not source-truth attestations."""
    try:
        view, expected = select_memory(original, strategy=strategy, max_bytes=max_bytes)
        return canonical(view) == canonical(selected) and canonical(expected) == canonical(receipt)
    except (ValueError, TypeError, KeyError, RecursionError, OSError):
        return False
