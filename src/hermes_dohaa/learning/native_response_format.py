"""Trusted fixed response schemas, not arbitrary caller-provided generation rules."""

DOCUMENT_RESPONSE_CONTRACT = 'document-stream-proposal/1.0'


def document_response_format():
    """Return fresh schema objects; never include case answers or source values.

    This constrains syntax and basic shape only. Reference truth, citation
    relevance/currentness, uniqueness and all local limits still need validation.
    """
    def object_schema(properties):
        return {'type': 'object', 'properties': properties,
                'required': list(properties), 'additionalProperties': False}
    citation = object_schema({'source_id': {'type': 'string'}, 'revision': {'type': 'integer'}})
    fact = object_schema({'key': {'type': 'string'}, 'value': {'type': 'string'},
                          'evidence': {'type': 'array', 'items': citation, 'minItems': 1, 'maxItems': 16}})
    result = object_schema({'facts': {'type': 'array', 'items': fact, 'maxItems': 128}})
    schema = object_schema({'result': result,
        'claims': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0},
        'evidence': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0},
        'requested_actions': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}})
    return {'type': 'json_schema', 'json_schema': {
        'name': 'hermes_document_stream_proposal_v1', 'strict': True, 'schema': schema}}


def response_format_for_policy(policy):
    version = policy.get('schema_version')
    if version == 'hermes-native-shadow-policy/1.0' and 'response_contract' not in policy:
        return None
    if (version in ('hermes-native-shadow-policy/1.1', 'hermes-native-shadow-policy/1.2')
            and policy.get('response_contract') == DOCUMENT_RESPONSE_CONTRACT):
        return document_response_format()
    raise ValueError('unsupported response contract')


def native_request_overrides(policy):
    overrides = {key: policy[key] for key in ('seed', 'temperature', 'top_p')}
    response_format = response_format_for_policy(policy)
    if response_format is not None:
        overrides['response_format'] = response_format
    return overrides
