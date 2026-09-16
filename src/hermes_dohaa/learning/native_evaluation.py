"""Fixed numeric evaluation envelope; no actions, arbitrary schemas or grading truth."""
import re
from decimal import Decimal
from . import shadow

POLICY_VERSION = 'hermes-native-evaluation-policy/1.0'
CONTRACT = 'native-numeric-answer/1.0'
NUMBER = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'


def numeric_response_format():
    return {'type': 'json_schema', 'json_schema': {
        'name': 'hermes_numeric_answer_v1', 'strict': True,
        'schema': {'type': 'object', 'properties': {
            'answer': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                       'pattern': '^' + NUMBER + '$'}},
            'required': ['answer'], 'additionalProperties': False}}}


def parse_numeric_answer(data):
    """Strict shape and exact decimal syntax; never extract numbers from prose."""
    if type(data) is not bytes or not 0 < len(data) <= 1024:
        raise ValueError('bounded_numeric_answer_required')
    value = shadow._json(data, shadow._hash(data))
    shadow._fields(value, {'answer'})
    answer = value['answer']
    if (type(answer) is not str or not 1 <= len(answer) <= 128
            or re.fullmatch(NUMBER, answer) is None):
        raise ValueError('decimal_answer_required')
    return Decimal(answer)
