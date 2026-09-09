"""Fixed, standalone model proposal boundary for the synthetic tool simulator.

Binding does not execute or reconcile an operation. Stable identities and the
immutable step envelope must come from a trusted host, never from model output.
The tool retains its independent authorization and state precondition checks.
"""
from dataclasses import dataclass
import json

from tools.controlled_tools import Operation

VERSION = 'controlled-tool-proposal/1.0'
MAX_BYTES = 16384
MAX_INTEGER = 2**31 - 1
ABSTENTION_REASONS = ('no_action', 'inventory_unavailable', 'insufficient_stock',
                      'conflicting_sources')
FIELDS = {'schema_version', 'decision', 'product', 'quantity', 'expected_version',
          'reservation_id', 'reason'}


def _identifier(value):
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise ValueError('invalid_identifier')


def _integer(value, minimum):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError('invalid_integer')


@dataclass(frozen=True)
class ReservationRef:
    reservation_id: str
    product: str
    quantity: int

    def __post_init__(self):
        _identifier(self.reservation_id)
        _identifier(self.product)
        _integer(self.quantity, 1)


@dataclass(frozen=True)
class HostStep:
    task_id: str
    step_id: str
    operation_id: str
    allowed_kinds: tuple[str, ...]
    allowed_products: tuple[str, ...]
    max_quantity: int
    reservations: tuple[ReservationRef, ...] = ()

    def __post_init__(self):
        for identifier in (self.task_id, self.step_id, self.operation_id):
            _identifier(identifier)
        _integer(self.max_quantity, 1)
        for sequence in (self.allowed_kinds, self.allowed_products, self.reservations):
            if type(sequence) is not tuple or len(sequence) > 128:
                raise ValueError('immutable_scope_required')
        if not self.allowed_kinds or not self.allowed_products:
            raise ValueError('empty_scope')
        for kind in self.allowed_kinds:
            if type(kind) is not str or kind not in ('reserve', 'release'):
                raise ValueError('unsupported_operation')
        for product in self.allowed_products:
            _identifier(product)
        if (len(set(self.allowed_kinds)) != len(self.allowed_kinds)
                or len(set(self.allowed_products)) != len(self.allowed_products)):
            raise ValueError('duplicate_scope')
        identifiers = set()
        for reference in self.reservations:
            if type(reference) is not ReservationRef:
                raise ValueError('invalid_reservation_reference')
            reference.__post_init__()
            if reference.product not in self.allowed_products or reference.reservation_id in identifiers:
                raise ValueError('reservation_outside_scope')
            identifiers.add(reference.reservation_id)


@dataclass(frozen=True)
class BoundProposal:
    operation: Operation | None
    reason: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('nonfinite_number')


def bind_tool_proposal(data: bytes, step: HostStep) -> BoundProposal:
    """Parse one proposal and bind host authority; never execute or mark success.

    Abstention produces no Operation. It cannot resolve a prior pending intent.
    Recovery, task completion and persistence of host steps are caller duties.
    """
    if type(step) is not HostStep:
        raise ValueError('host_step_required')
    step.__post_init__()
    if type(data) is not bytes or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('invalid_proposal_bytes')
    try:
        proposal = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError('invalid_proposal_json') from exc
    if type(proposal) is not dict or set(proposal) != FIELDS:
        raise ValueError('invalid_proposal_fields')
    if proposal['schema_version'] != VERSION:
        raise ValueError('invalid_proposal_version')
    kind = proposal['decision']
    reason = proposal['reason']
    if type(kind) is not str or type(reason) is not str:
        raise ValueError('invalid_decision')
    if kind == 'abstain':
        if (reason not in ABSTENTION_REASONS
                or any(proposal[key] is not None for key in
                       ('product', 'quantity', 'expected_version', 'reservation_id'))):
            raise ValueError('invalid_abstention')
        return BoundProposal(None, reason)
    if kind not in step.allowed_kinds or reason != 'none':
        raise ValueError('operation_outside_scope')
    product = proposal['product']
    quantity = proposal['quantity']
    version = proposal['expected_version']
    reservation = proposal['reservation_id']
    _identifier(product)
    _integer(quantity, 1)
    _integer(version, 0)
    if product not in step.allowed_products or quantity > step.max_quantity:
        raise ValueError('product_or_quantity_outside_scope')
    if kind == 'reserve':
        if reservation is not None:
            raise ValueError('reserve_has_no_prior_reservation')
    else:
        _identifier(reservation)
        if not any((ref.reservation_id, ref.product, ref.quantity) ==
                   (reservation, product, quantity) for ref in step.reservations):
            raise ValueError('release_outside_scope')
    return BoundProposal(Operation(step.operation_id, step.task_id, step.step_id,
                                   kind, product, quantity, version, reservation), reason)


def tool_response_format():
    """Fresh fixed schema; branch semantics and host permissions remain in parser.

    This is not yet registered with the native document-only adapter. A later
    integration must verify the actual wire contract and provider compatibility.
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
