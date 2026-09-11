"""Bounded structured evidence resolution under an external public contract.

No model, oracle, authority inference, text extraction or memory selection.
Resolution establishes what permitted records assert, not real-world truth.
"""
import hashlib
import json

from tools.document_stream import identifier,nonnegative_integer
from tools.document_stream_projection import current_document_view
from tools.document_stream_request import canonical

MAX_SOURCES=12
MAX_RECORDS=32
MAX_DEPTH=16


def digest(data):return hashlib.sha256(data).hexdigest()


def _fields(value,fields):
    if type(value) is not dict or set(value)!=set(fields):raise ValueError('invalid fields')


def _text(value):return type(value) is str and bool(value.strip()) and len(value)<=512


def _ids(value,limit):
    if (type(value) is not list or not 1<=len(value)<=limit
            or any(not identifier(item) for item in value) or len(set(value))!=len(value)):
        raise ValueError('invalid identifiers')
    return value


def _contract(value,keys):
    _fields(value,('schema_version','allowed_sources','queries'))
    if value['schema_version']!='hermes-evidence-query/1.0':raise ValueError('invalid query schema')
    allowed=set(_ids(value['allowed_sources'],MAX_SOURCES))
    queries=value['queries']
    if type(queries) is not list or len(queries)!=len(keys):raise ValueError('query keys differ')
    seen=set()
    for query in queries:
        _fields(query,('key','root_sources'))
        if not identifier(query['key']) or query['key'] in seen:raise ValueError('invalid query key')
        seen.add(query['key'])
        if not set(_ids(query['root_sources'],MAX_SOURCES))<=allowed:raise ValueError('unpermitted query source')
    if seen!=set(keys):raise ValueError('query keys differ')
    return {query['key']:query for query in queries},allowed


def _reference(value):
    _fields(value,('source_id','revision','record_id'))
    if (not identifier(value['source_id']) or not identifier(value['record_id'])
            or not nonnegative_integer(value['revision']) or value['revision']==0):
        raise ValueError('invalid record reference')
    return value['source_id'],value['revision'],value['record_id']


def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate JSON field')
        value[key]=item
    return value


def _constant(value):raise ValueError('nonfinite JSON number')


def _records(text):
    raw=json.loads(text,object_pairs_hook=_unique,parse_constant=_constant)
    _fields(raw,('schema_version','records'))
    if raw['schema_version']!='hermes-evidence-records/1.0':raise ValueError('invalid record schema')
    if type(raw['records']) is not list or len(raw['records'])>MAX_RECORDS:raise ValueError('record count')
    found={}
    fields={'value':('value',),'table':('entries',),'link':('target',),'lookup':('input','table')}
    for record in raw['records']:
        if type(record) is not dict or type(record.get('kind')) is not str or record['kind'] not in fields:
            raise ValueError('invalid record kind')
        _fields(record,('id','key','kind','status',*fields[record['kind']]))
        if (not identifier(record['id']) or not identifier(record['key']) or record['id'] in found
                or type(record['status']) is not str or record['status'] not in ('asserted','example','negated')):
            raise ValueError('invalid record identity or status')
        kind=record['kind']
        if kind=='value' and not _text(record['value']):raise ValueError('invalid value')
        if kind=='table':
            entries=record['entries']
            if type(entries) is not dict or len(entries)>64 or any(not _text(k) or not _text(v) for k,v in entries.items()):
                raise ValueError('invalid lookup entries')
        if kind=='link':_reference(record['target'])
        if kind=='lookup':
            _reference(record['input']);_reference(record['table'])
        found[record['id']]=record
    return found


class _Unresolved(ValueError):
    pass


def _evidence(group):return [dict(source_id=source,revision=revision) for source,revision in sorted(group)]


def resolve_records(request,query_contract):
    """Resolve literal/link/lookup records without selecting or calling an LLM.

    Any malformed active permitted source makes the result out_of_scope; it
    cannot be silently discarded to remove a possible conflict. Every nominated
    root source must be active, and every matching root must resolve. Nonasserted
    matching roots, missing dependencies and conflicting values cause abstention.
    Dependencies identify specific records, not inferred same-key entities.
    """
    active,projection=current_document_view(request)
    keys=active['task']['fact_keys'];queries,allowed=_contract(query_contract,keys)
    events={event['source_id']:event for event in active['observation']['events']}
    documents={};diagnostics=[]
    for source in sorted(allowed & set(events)):
        try:documents[source]=_records(events[source]['text'])
        except (ValueError,TypeError,RecursionError,OverflowError):
            diagnostics.append(dict(source_id=source,code='record_source_out_of_scope'))
    result=dict(schema_version='hermes-evidence-resolution/1.0',input_sha256=projection['input_sha256'],
        contract_sha256=digest(canonical(query_contract)),active_sha256=digest(canonical(active)),
        status='unresolved',complete=False,facts=[],queries=[],diagnostics=diagnostics,
        excluded_sources=sorted(set(events)-allowed),native_requests=0,semantic_truth_verified=False)
    if diagnostics:
        result['status']='out_of_scope'
        result['queries']=[dict(key=key,status='out_of_scope',groups=[],reasons=['record_source_out_of_scope']) for key in keys]
        return result
    cache={}

    def resolve(node,visiting=frozenset(),depth=0):
        if depth>MAX_DEPTH:raise _Unresolved('depth_limit')
        if node in visiting:raise _Unresolved('cycle')
        if node in cache:
            if depth+cache[node][2]>MAX_DEPTH:raise _Unresolved('depth_limit')
            return cache[node]
        source,revision,record_id=node
        if source not in allowed:raise _Unresolved('source_not_permitted')
        if source not in events:raise _Unresolved('source_inactive')
        if events[source]['revision']!=revision:raise _Unresolved('wrong_revision')
        record=documents[source].get(record_id)
        if record is None:raise _Unresolved('missing_record')
        if record['status']!='asserted':raise _Unresolved('not_asserted')
        group=frozenset(((source,revision),));height=0
        kind=record['kind'];path=visiting|{node}
        if kind=='value':value=record['value']
        elif kind=='table':value=record['entries']
        elif kind=='link':
            value,dependencies,height=resolve(_reference(record['target']),path,depth+1)
            group=group|dependencies;height+=1
        else:
            input_value,input_group,input_height=resolve(_reference(record['input']),path,depth+1)
            entries,table_group,table_height=resolve(_reference(record['table']),path,depth+1)
            if type(input_value) is not str or type(entries) is not dict:raise _Unresolved('wrong_operand_type')
            if input_value not in entries:raise _Unresolved('missing_mapping')
            value=entries[input_value];group=group|input_group|table_group;height=1+max(input_height,table_height)
        if depth+height>MAX_DEPTH:raise _Unresolved('depth_limit')
        cache[node]=(value,group,height)
        return cache[node]

    for key in keys:
        roots=queries[key]['root_sources'];reasons=set();candidates=[]
        if any(source not in documents for source in roots):reasons.add('source_inactive')
        nodes=[(source,events[source]['revision'],record_id) for source in roots if source in documents
               for record_id,record in documents[source].items() if record['key']==key]
        if not nodes:reasons.add('missing_record')
        for node in nodes:
            try:
                value,group,_=resolve(node)
                if type(value) is not str:raise _Unresolved('wrong_operand_type')
                candidates.append((value,tuple(sorted(group))))
            except _Unresolved as error:reasons.add(str(error))
        groups=[dict(value=value,evidence=_evidence(group)) for value,group in sorted(set(candidates))]
        values={value for value,_ in candidates}
        conflict=len(values)>1
        if conflict:reasons.add('conflicting_values')
        status='conflict' if conflict else 'unresolved' if reasons else 'resolved'
        row=dict(key=key,status=status,reasons=sorted(reasons),groups=groups)
        result['queries'].append(row)
        if status=='resolved':
            value,group=min(set(candidates),key=lambda pair:(len(pair[1]),pair[1]))
            result['facts'].append(dict(key=key,value=value,evidence=_evidence(group)))
    result['complete']=len(result['facts'])==len(keys)
    result['status']='resolved' if result['complete'] else 'partial' if result['facts'] else 'unresolved'
    # Return a fresh JSON-only result without sharing mutable tables or inputs.
    return json.loads(canonical(result))
