"""One fresh native memory-pilot call and evaluator-side original-source scoring.

The external frozen pilot owns ordering, case commitments and the global budget.
This module never retries a call or repairs a model answer. Each call has a new
root-owned directory, durable instance lease and protected native evidence.
"""
import base64
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from hermes_dohaa.learning import native_prompt as native, shadow
from hermes_dohaa.learning.native_reasoning import binary_model_sha256
from tools.document_stream import DocumentEvent
from tools.document_stream_projection import current_document_view
from tools.document_stream_request import (canonical, parse_submission, task_contract,
                                           StreamCitationGate, validate_request)
from tools.document_stream_scoring import ReferenceFact, score_report
from tools.native_model_residency import ModelResidency


def _save(path, value):
    shadow._publish(path, canonical(value))


def usage(wire, model):
    """Same verified JSON/SSE accounting rule as the multistep collector."""
    native.parse_wire_response(wire, model)
    documents = ([json.loads(line[5:].strip()) for line in wire.splitlines()
                  if line.strip() and line[5:].strip() != b'[DONE]']
                 if wire.lstrip().startswith(b'data:') else [json.loads(wire)])
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    reported = None
    for document in documents:
        counts = document.get('usage')
        if counts is None:
            continue
        if type(counts) is not dict or any(type(counts.get(k)) is not int or counts[k] < 0 for k in keys):
            return None
        current = {key: counts[key] for key in keys}
        if reported is not None and current != reported:
            return None
        reported = current
    return reported


def score_terminal(original, selected, references, terminal):
    """Missing selected evidence cannot erase requirements from the evaluator."""
    original, selected = validate_request(original), validate_request(selected)
    if (canonical(original['task']) != canonical(selected['task'])
            or original['observation']['run_id'] != selected['observation']['run_id']
            or original['observation']['at_tick'] != selected['observation']['at_tick']):
        raise ValueError('memory task changed')
    full_active, _ = current_document_view(original)
    active = {raw['source_id']: DocumentEvent(**raw) for raw in full_active['observation']['events']}
    refs = [ReferenceFact(ref['key'], ref['value'],
                         tuple(tuple(tuple(pair) for pair in group) for group in ref['evidence']))
            for ref in references]
    result = dict(format_valid=False, accepted=False, correct=False, incorrect_accepted=False,
                  score=score_report({}, refs, active))
    if terminal['status'] == 'failed':
        if terminal.get('error_code') not in ('budget_exhausted', 'runtime_error'):
            raise ValueError('unsupported terminal failure')
        return dict(result, error_code=terminal['error_code'])
    if terminal['status'] != 'completed':
        raise ValueError('unknown completion cannot be scored')
    try:
        proposal = parse_submission(terminal['content'])
    except (ValueError, TypeError, KeyError):
        return dict(result, error_code='invalid_response')
    accepted = StreamCitationGate().evaluate(task_contract(selected), proposal).passed
    score = score_report(proposal.result, refs, active)
    return dict(format_valid=True, accepted=accepted, correct=accepted and score['task_success'],
                incorrect_accepted=accepted and not score['task_success'], score=score)


def record_call(root, adapter, request_bytes, expected_model_sha256):
    """Record one native request, then unload before optional accounting/scoring.

    Catalog identity excludes residency only. It is a host observation, not a
    server-effective reasoning attestation. Setup verifies the installed native
    source through the existing adapter. No secret enters returned metadata.
    """
    root = native.protected_directory(root)
    if (type(adapter) is not native.NativePromptAdapter or adapter.state != 'new'
            or adapter.evidence_dir.absolute() != root/'native'
            or adapter.policy['schema_version'] != 'hermes-native-shadow-policy/1.2'
            or adapter.policy['response_contract'] != 'document-stream-proposal/1.0'
            or adapter.policy['reasoning_effort'] != 'none'):
        raise ValueError('fresh document adapter required')
    native.validate_request(request_bytes, adapter.collection_sha256)
    shadow._digest(expected_model_sha256)
    request_sha = shadow._hash(request_bytes)
    _save(root/'started.private.json', dict(request_sha256=request_sha,
          policy_sha256=adapter.runtime_policy_sha256, collection_sha256=adapter.collection_sha256,
          model_sha256=expected_model_sha256))
    lease = None
    started = time.monotonic_ns()
    result = dict(status='incomplete', actual_requests=None, exact_unload=False,
                  effective_mode_attested=False, request_sha256=request_sha, usage=None)
    try:
        p = adapter.policy
        url = urlsplit(p['endpoint']); models = f'{url.scheme}://{url.netloc}/api/v1/models'
        def catalog():
            raw = adapter._http(models)
            if binary_model_sha256(canonical(raw), p['model']) != expected_model_sha256:
                raise ValueError('committed model changed')
            return raw
        _save(root/'catalog-before.private.json', catalog())
        adapter.start()
        lease = ModelResidency(root/'residency.db', model=p['model'], context_length=p['context_length'],
                    policy_sha256=adapter.runtime_policy_sha256, catalog=catalog,
                    load=lambda body: adapter._http(models+'/load', body),
                    unload=lambda body: adapter._http(models+'/unload', body))
        t = time.monotonic_ns(); lease.start()
        result['load_ms'] = (time.monotonic_ns()-t)//1_000_000
        adapter.owned = {lease.snapshot()['instance_id']}
        lease.begin_generation(request_sha)
        t = time.monotonic_ns(); receipt = adapter.generate(request_bytes)
        result['generation_ms'] = (time.monotonic_ns()-t)//1_000_000
        trace_bytes = native.protected_read(root/'native/worker-0000.json')
        checked = native.verify_worker_terminal(trace_bytes, request_bytes, adapter.collection_sha256,
                                                p, adapter.runtime_policy_sha256, 0)
        if receipt != checked:
            raise ValueError('native receipt changed')
        # The adapter returned only after checking the actual process returncode.
        shadow._publish(root/'receipt.private.json', receipt)
        lease.record_terminal(request_sha, receipt)
        native.protected_directory(adapter.worker_root/'profile-0')
        _save(root/'witness.private.json', dict(request_sha256=request_sha,
              trace_sha256=shadow._hash(trace_bytes), receipt_sha256=shadow._hash(receipt),
              policy_sha256=adapter.runtime_policy_sha256, collection_sha256=adapter.collection_sha256,
              model_sha256=expected_model_sha256, instance_id=lease.snapshot()['instance_id'],
              load_receipt_sha256=lease.snapshot()['load_receipt_sha'],
              native_commit=p['native_commit'], bridge_sha256=p['bridge_sha256']))
        t = time.monotonic_ns(); lease.finish()
        result['unload_ms'] = (time.monotonic_ns()-t)//1_000_000
        result['exact_unload'] = lease.snapshot()['state'] == 'closed'
        adapter.owned.clear(); adapter.state = 'closed'
        # Optional accounting cannot strand a known completed model or trigger
        # replacement generation; the exact owned unload is already committed.
        trace = json.loads(trace_bytes)
        wire = base64.b64decode(trace['wire_response_base64'], validate=True)
        parsed = native.parse_wire_response(wire, p['model'])
        terminal = json.loads(receipt)
        result.update(actual_requests=trace['actual_requests'], terminal_status=terminal['status'],
                      receipt_sha256=shadow._hash(receipt), trace_sha256=shadow._hash(trace_bytes),
                      finish_reason=parsed['finish_reason'], reasoning_characters=parsed['reasoning_characters'],
                      usage=usage(wire, p['model']), status='completed')
        if terminal['status'] == 'failed': result['error_code'] = terminal['error_code']
    except Exception as error:
        result['exception_class'] = type(error).__name__
        code = getattr(error, 'code', None)
        if type(code) is str and code.startswith(('native_shadow.', 'native_tool.', 'shadow.')):
            result['error_code'] = code
        if lease is not None and lease.snapshot()['state'] in ('ready', 'loaded_invalid'):
            try:
                lease.finish(); result['exact_unload'] = lease.snapshot()['state'] == 'closed'
            except Exception:
                pass
    finally:
        result['elapsed_ms'] = (time.monotonic_ns()-started)//1_000_000
        result['residency_state'] = lease.snapshot()['state'] if lease is not None else None
        _save(root/'result.private.json', result)
    return result
