"""Bounded serial native requests sharing exactly one owned model load.

The existing adapter creates a fresh worker profile for every request. This
collector retains each verified terminal and closes the owned instance once.
It neither retries requests nor changes their order based on answer quality.
"""
import base64
import json
import time
from urllib.parse import urlsplit

from hermes_dohaa.learning import native_prompt as native,shadow
from hermes_dohaa.learning.native_reasoning import binary_model_sha256
from tools.document_stream_request import canonical
from tools.native_memory_pilot import usage
from tools.native_model_residency import ModelResidency


def _save(path,value):shadow._publish(path,canonical(value))


def record_block(root,adapter,requests,expected_model_sha256,*,wall_seconds,no_new_call_margin_seconds):
    """Record 1..8 committed requests, then exact-unload before accounting.

    Scheduling time bounds admission of another call, not server cancellation.
    The outer campaign still owns source/case/order commitments and its global
    budget. A completed block may contain known budget/runtime failure outcomes.
    Missing usage remains unmeasured. No effective reasoning mode is attested.
    """
    root=native.protected_directory(root)
    if (type(adapter) is not native.NativePromptAdapter or adapter.state!='new'
            or adapter.evidence_dir.absolute()!=root/'native'
            or adapter.policy['schema_version']!='hermes-native-shadow-policy/1.2'
            or adapter.policy['response_contract']!='document-stream-proposal/1.0'
            or adapter.policy['reasoning_effort']!='none'):
        raise ValueError('fresh document adapter required')
    p=adapter.policy
    if (type(requests) not in (tuple,list) or not 1<=len(requests)<=min(8,p['request_limit'])
            or type(wall_seconds) is not int or not 1<=wall_seconds<=10800
            or type(no_new_call_margin_seconds) is not int
            or not p['worker_timeout_seconds']<=no_new_call_margin_seconds<wall_seconds):
        raise ValueError('invalid block budget')
    requests=tuple(requests)
    if any(type(rb) is not bytes for rb in requests):raise ValueError('request bytes required')
    logical=[native.validate_request(rb,adapter.collection_sha256) for rb in requests]
    hashes=[shadow._hash(rb) for rb in requests]
    if len(set(hashes))!=len(hashes) or len({req['request_id'] for req in logical})!=len(logical):
        raise ValueError('duplicate block request')
    shadow._digest(expected_model_sha256)
    _save(root/'started.private.json',dict(request_sha256=hashes,policy_sha256=adapter.runtime_policy_sha256,
          collection_sha256=adapter.collection_sha256,model_sha256=expected_model_sha256,
          wall_seconds=wall_seconds,no_new_call_margin_seconds=no_new_call_margin_seconds))
    lease=None;started=time.monotonic_ns();verified=[]
    result=dict(status='incomplete',planned_requests=len(requests),attempted_requests=0,verified_requests=0,
        unresolved_attempts=0,unattempted_requests=len(requests),exact_unload=False,effective_mode_attested=False,
        calls=[],load_ms=None,unload_ms=None,stop_reason=None)
    try:
        (root/'calls').mkdir(mode=0o700)
        url=urlsplit(p['endpoint']);models=f'{url.scheme}://{url.netloc}/api/v1/models'
        def catalog():
            raw=adapter._http(models)
            if binary_model_sha256(canonical(raw),p['model'])!=expected_model_sha256:
                raise ValueError('committed model changed')
            return raw
        _save(root/'catalog-before.private.json',catalog())
        adapter.start()
        lease=ModelResidency(root/'residency.db',model=p['model'],context_length=p['context_length'],
            policy_sha256=adapter.runtime_policy_sha256,catalog=catalog,
            load=lambda body:adapter._http(models+'/load',body),unload=lambda body:adapter._http(models+'/unload',body))
        t=time.monotonic_ns();lease.start();result['load_ms']=(time.monotonic_ns()-t)//1_000_000
        adapter.owned={lease.snapshot()['instance_id']}
        for index,(rb,request_sha) in enumerate(zip(requests,hashes)):
            if (time.monotonic_ns()-started)/1_000_000_000>=wall_seconds-no_new_call_margin_seconds:
                result['stop_reason']='wall_budget';break
            path=root/'calls'/f'{index:03d}';path.mkdir(mode=0o700)
            _save(path/'started.private.json',dict(index=index,request_sha256=request_sha))
            lease.begin_generation(request_sha)
            result['attempted_requests']+=1
            t=time.monotonic_ns();receipt=adapter.generate(rb);elapsed=(time.monotonic_ns()-t)//1_000_000
            trace_bytes=native.protected_read(root/'native'/f'worker-{index:04d}.json')
            if receipt!=native.verify_worker_terminal(trace_bytes,rb,adapter.collection_sha256,p,adapter.runtime_policy_sha256,0):
                raise ValueError('native receipt changed')
            # Actual process returncode was verified inside adapter.generate.
            shadow._publish(path/'receipt.private.json',receipt)
            lease.record_terminal(request_sha,receipt)
            profile=native.protected_directory(adapter.worker_root/f'profile-{index}')
            state=lease.snapshot()
            witness=dict(request_sha256=request_sha,trace_sha256=shadow._hash(trace_bytes),receipt_sha256=shadow._hash(receipt),
                policy_sha256=adapter.runtime_policy_sha256,collection_sha256=adapter.collection_sha256,
                model_sha256=expected_model_sha256,instance_id=state['instance_id'],load_receipt_sha256=state['load_receipt_sha'],
                native_commit=p['native_commit'],bridge_sha256=p['bridge_sha256'],worker_index=index,profile=str(profile))
            _save(path/'witness.private.json',witness)
            terminal=json.loads(receipt)
            row=dict(index=index,request_sha256=request_sha,receipt_sha256=shadow._hash(receipt),
                trace_sha256=shadow._hash(trace_bytes),terminal_status=terminal['status'],generation_ms=elapsed,
                resident_position='first' if index==0 else 'subsequent',usage=None)
            if terminal['status']=='failed':row['error_code']=terminal['error_code']
            _save(path/'terminal.private.json',row)
            verified.append((row,trace_bytes));result['verified_requests']=len(verified)
    except Exception as error:
        result['exception_class']=type(error).__name__
        code=getattr(error,'code',None)
        if type(code) is str and code.startswith(('native_shadow.','native_tool.','shadow.')):result['error_code']=code
    finally:
        # An unknown generation or load/unload result is deliberately untouched.
        # A failed unload is never repeated automatically in this invocation.
        if lease is not None and lease.snapshot()['state'] in ('ready','loaded_invalid'):
            try:
                t=time.monotonic_ns();lease.finish();result['unload_ms']=(time.monotonic_ns()-t)//1_000_000
                result['exact_unload']=lease.snapshot()['state']=='closed'
                adapter.owned.clear();adapter.state='closed'
            except Exception as error:result['cleanup_exception_class']=type(error).__name__
        result['residency_state']=lease.snapshot()['state'] if lease is not None else None
        result['unresolved_attempts']=result['attempted_requests']-result['verified_requests']
        result['unattempted_requests']=len(requests)-result['attempted_requests']
        # Optional token parsing is postponed until cleanup. It cannot change
        # which requests execute, strand a known terminal, or cause a replacement.
        for row,trace_bytes in verified:
            item=dict(row)
            try:
                trace=json.loads(trace_bytes);wire=base64.b64decode(trace['wire_response_base64'],validate=True)
                parsed=native.parse_wire_response(wire,p['model'])
                item.update(usage=usage(wire,p['model']),finish_reason=parsed['finish_reason'],
                            reasoning_characters=parsed['reasoning_characters'])
            except Exception as error:
                item['accounting_error_class']=type(error).__name__
                result['accounting_failed']=True
            result['calls'].append(item)
        if (result['verified_requests']==len(requests) and result['exact_unload']
                and not any(key in result for key in ('exception_class','cleanup_exception_class','accounting_failed'))):
            result['status']='completed'
        result['elapsed_ms']=(time.monotonic_ns()-started)//1_000_000
        _save(root/'result.private.json',result)
    return result
