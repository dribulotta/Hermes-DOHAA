"""Single fixed syntax contract shared by the native worker and host parser."""
TOOL_POLICY_VERSION = "hermes-native-tool-policy/1.0"
VERSION = 'controlled-tool-proposal/1.0'
MAX_BYTES = 16384
MAX_INTEGER = 2**31 - 1
ABSTENTION_REASONS = ('no_action', 'inventory_unavailable', 'insufficient_stock',
                      'conflicting_sources')
FIELDS = {'schema_version', 'decision', 'product', 'quantity', 'expected_version',
          'reservation_id', 'reason'}


def tool_response_format():
    """Fresh fixed schema; branch semantics and host permissions remain in parser.

    The native tool policy uses this exact schema; it grants no permissions.
    """
    identifier = lambda: {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 128}
    integer = lambda minimum: {'type': ['integer', 'null'], 'minimum': minimum,
                               'maximum': MAX_INTEGER}
    properties = {
        'schema_version': {'type': 'string', 'enum': [VERSION]},
        'decision': {'type': 'string', 'enum': ['reserve', 'release', 'abstain']},
        'product': identifier(), 'quantity': integer(1),
        'expected_version': integer(0), 'reservation_id': identifier(),
        'reason': {'type': 'string', 'enum': ['none', *ABSTENTION_REASONS]},
    }
    return {'type': 'json_schema', 'json_schema': {
        'name': 'hermes_controlled_tool_proposal_v1', 'strict': True,
        'schema': {'type': 'object', 'additionalProperties': False,
                   'properties': properties, 'required': list(properties)}}}
