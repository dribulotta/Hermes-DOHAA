"""Deterministic pre-response profile choices and worst-case generation caps.

Host metadata is trusted input, not authenticated ground truth. This planner
does not load a model, dispatch requests, enforce runtime spend, or infer quality.
"""
import base64
from pathlib import Path

from hermes_dohaa.learning import native_prompt as native,shadow

MAX_DOCUMENT_BYTES=1024*1024
MAX_TASKS=256
MAX_REQUIRED_ITEMS=512
MAX_SOURCE_RECORDS=2048
MAX_OPERATION_STEPS=128
MAX_REQUESTS=8
MAX_RULES=16
MAX_INTEGER=2**31-1
PROFILES=('none','medium')
FEATURE_LIMITS=dict(required_items=MAX_REQUIRED_ITEMS,source_records=MAX_SOURCE_RECORDS,
                    revision_transitions=MAX_SOURCE_RECORDS,operation_steps=MAX_OPERATION_STEPS)


def source_sha256():
    return shadow._hash(Path(__file__).read_bytes().replace(b'\r\n',b'\n'))


def _document(raw,expected_sha256):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_DOCUMENT_BYTES:
        raise ValueError('bounded_document_bytes_required')
    value=shadow._json(raw,expected_sha256)
    if raw!=shadow._canonical(value):
        raise ValueError('canonical_document_required')
    return value


def _text(value):
    if type(value) is not str or not value.strip() or len(value.encode('utf-8'))>128:
        raise ValueError('bounded_identifier_required')


def _integer(value,low,high):
    if type(value) is not int or not low<=value<=high:
        raise ValueError('bounded_integer_required')


def _list(value,maximum,*,nonempty=False):
    if type(value) is not list or not int(nonempty)<=len(value)<=maximum:
        raise ValueError('bounded_list_required')


def _identifiers(value,maximum,*,nonempty=False):
    _list(value,maximum,nonempty=nonempty)
    seen=set()
    for item in value:
        _text(item)
        if item in seen:raise ValueError('duplicate_identifier')
        seen.add(item)
    return seen


def _embedded(value):
    shadow._fields(value,{'document_base64','document_sha256'})
    if type(value['document_base64']) is not str:
        raise ValueError('encoded_document_required')
    raw=base64.b64decode(value['document_base64'],validate=True)
    return raw,_document(raw,value['document_sha256'])


def _descriptor(raw,expected_sha256):
    d=_document(raw,expected_sha256)
    shadow._fields(d,{'schema_version','task_id','public_input_sha256','required_items',
                      'source_records','operation_steps','planned_request_ids'})
    if d['schema_version']!='hermes-budget-task/1.0':raise ValueError('task_descriptor_version_required')
    _text(d['task_id']);shadow._digest(d['public_input_sha256'])
    _identifiers(d['required_items'],MAX_REQUIRED_ITEMS,nonempty=True)
    _identifiers(d['operation_steps'],MAX_OPERATION_STEPS)
    _identifiers(d['planned_request_ids'],MAX_REQUESTS,nonempty=True)
    _list(d['source_records'],MAX_SOURCE_RECORDS)
    latest={}
    for record in d['source_records']:
        shadow._fields(record,{'source_id','revision'})
        _text(record['source_id']);_integer(record['revision'],0,MAX_INTEGER)
        if record['source_id'] in latest and record['revision']<=latest[record['source_id']]:
            raise ValueError('source_revision_order_or_identity_changed')
        latest[record['source_id']]=record['revision']
    features=dict(required_items=len(d['required_items']),source_records=len(d['source_records']),
                  revision_transitions=len(d['source_records'])-len(latest),operation_steps=len(d['operation_steps']))
    return d,features


def derive_features(descriptor_bytes,expected_sha256):
    """Count bounded metadata identities; never interpret their text or answers."""
    d,features=_descriptor(descriptor_bytes,expected_sha256)
    return shadow._canonical(dict(schema_version='hermes-budget-features/1.0',source_sha256=source_sha256(),
        task_id=d['task_id'],descriptor_sha256=expected_sha256,public_input_sha256=d['public_input_sha256'],features=features))


def _profiles(raw,expected_sha256):
    library=_document(raw,expected_sha256)
    shadow._fields(library,{'schema_version','profiles'})
    if library['schema_version']!='hermes-reasoning-profiles/1.0':raise ValueError('profile_library_version_required')
    shadow._fields(library['profiles'],set(PROFILES))
    policies={};identities={};common=None
    for name in PROFILES:
        encoded=library['profiles'][name];data,_=_embedded(encoded)
        policy=native.validate_native_policy(data,encoded['document_sha256'])
        if 'context_length' not in policy or 'response_contract' not in policy:
            raise ValueError('explicit_context_and_contract_required')
        if policy['reasoning_effort']!=name:raise ValueError('profile_mode_mismatch')
        shared=shadow._canonical({key:value for key,value in policy.items() if key not in ('reasoning_effort','max_tokens')})
        if common is not None and shared!=common:raise ValueError('profile_nonfactor_settings_changed')
        common=shared;policies[name]=policy;identities[name]=encoded['document_sha256']
    return policies,identities


def _rules(raw,expected_sha256):
    table=_document(raw,expected_sha256)
    shadow._fields(table,{'schema_version','default_profile','rules'})
    if table['schema_version']!='hermes-reasoning-rules/1.0':raise ValueError('rule_table_version_required')
    if table['default_profile'] not in PROFILES:raise ValueError('known_profile_required')
    _list(table['rules'],MAX_RULES);identities=set()
    for rule in table['rules']:
        shadow._fields(rule,{'rule_id','feature','at_least','profile'})
        _text(rule['rule_id'])
        if rule['rule_id'] in identities:raise ValueError('duplicate_rule')
        identities.add(rule['rule_id'])
        if type(rule['feature']) is not str or rule['feature'] not in FEATURE_LIMITS:raise ValueError('known_feature_required')
        _integer(rule['at_least'],0,FEATURE_LIMITS[rule['feature']])
        if rule['profile'] not in PROFILES:raise ValueError('known_profile_required')
    return table


def build_plan(*,workload_bytes,workload_sha256,profiles_bytes,profiles_sha256,rules_bytes,rules_sha256,
               token_ceiling,strategy='adaptive'):
    """Return a complete canonical reservation manifest, or reject the whole plan.

    The coordinator must commit this and bind actual requests before dispatch.
    A manifest is not execution authority and cannot refund or reuse a request.
    """
    _integer(token_ceiling,0,MAX_INTEGER)
    if type(strategy) is not str or strategy not in ('adaptive','fixed-none','fixed-medium'):
        raise ValueError('known_strategy_required')
    policies,profile_ids=_profiles(profiles_bytes,profiles_sha256)
    rules=_rules(rules_bytes,rules_sha256)
    workload=_document(workload_bytes,workload_sha256)
    shadow._fields(workload,{'schema_version','tasks'})
    if workload['schema_version']!='hermes-budget-workload/1.0':raise ValueError('workload_version_required')
    _list(workload['tasks'],MAX_TASKS,nonempty=True)
    tasks=set();requests=set();entries=[];reserved=0
    for embedded in workload['tasks']:
        raw,_=_embedded(embedded);d,features=_descriptor(raw,embedded['document_sha256'])
        if d['task_id'] in tasks or requests.intersection(d['planned_request_ids']):
            raise ValueError('task_or_request_identity_reused')
        tasks.add(d['task_id']);requests.update(d['planned_request_ids'])
        matched=None
        if strategy=='adaptive':
            choice=rules['default_profile']
            for rule in rules['rules']:
                if features[rule['feature']]>=rule['at_least']:
                    matched=rule['rule_id'];choice=rule['profile'];break
        else:choice=strategy.removeprefix('fixed-')
        count=len(d['planned_request_ids']);policy=policies[choice]
        if count>policy['request_limit']:raise ValueError('profile_request_limit_exceeded')
        allowance=count*policy['max_tokens'];reserved+=allowance
        if reserved>token_ceiling:raise ValueError('generation_ceiling_exceeded')
        features_raw=derive_features(raw,embedded['document_sha256'])
        entries.append(dict(task_id=d['task_id'],descriptor_sha256=embedded['document_sha256'],
            public_input_sha256=d['public_input_sha256'],features_sha256=shadow._hash(features_raw),profile=choice,
            profile_sha256=profile_ids[choice],matched_rule=matched,max_tokens=policy['max_tokens'],
            planned_request_ids=d['planned_request_ids'],reserved_generation_tokens=allowance))
    return shadow._canonical(dict(schema_version='hermes-reasoning-budget-plan/1.0',source_sha256=source_sha256(),
        workload_sha256=workload_sha256,profiles_sha256=profiles_sha256,rules_sha256=rules_sha256,strategy=strategy,
        token_ceiling=token_ceiling,reserved_generation_tokens=reserved,remaining_generation_tokens=token_ceiling-reserved,
        task_count=len(entries),request_count=len(requests),entries=entries))


def validate_plan(raw,expected_sha256,**build_arguments):
    """Recompute against the original commitments, not a caller-edited manifest."""
    value=_document(raw,expected_sha256)
    if raw!=build_plan(**build_arguments):raise ValueError('plan_differs_from_committed_inputs')
    return value
