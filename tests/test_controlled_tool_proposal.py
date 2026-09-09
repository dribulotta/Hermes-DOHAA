"""Untrusted proposal parsing and host-bound synthetic operation regressions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.controlled_tools import Conflict, Executor, IntentJournal, ToolStore
from tools.controlled_tool_proposal import (HostStep, ReservationRef, bind_tool_proposal,
                                            tool_response_format)


def host(**changes):
    values = dict(task_id='task-a', step_id='step-1', operation_id='op-1',
                  allowed_kinds=('reserve', 'release'), allowed_products=('widget',),
                  max_quantity=5, reservations=())
    values.update(changes)
    return HostStep(**values)


def raw(**changes):
    values = dict(schema_version='controlled-tool-proposal/1.0', decision='reserve',
                  product='widget', quantity=4, expected_version=0,
                  reservation_id=None, reason='none')
    values.update(changes)
    return json.dumps(values).encode()


class ToolProposalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.tool = ToolStore(root/'tool.sqlite3')
        self.journal = IntentJournal(root/'intent.sqlite3')
        self.executor = Executor(self.journal, self.tool)
        self.tool.seed('widget', 10)
        self.tool.authorize('task-a', 'widget', 7)

    def test_binding_uses_host_identity_and_has_no_effect(self):
        result = bind_tool_proposal(raw(), host())
        op = result.operation
        self.assertEqual((op.task_id, op.step_id, op.operation_id), ('task-a', 'step-1', 'op-1'))
        self.assertIsNone(self.tool.lookup('op-1'))
        self.assertIsNone(self.journal.pending('task-a'))
        receipt = self.executor.execute(op)
        self.assertEqual(receipt['status'], 'applied')
        self.assertEqual(self.tool.inventory('widget')['available'], 6)

    def test_model_cannot_supply_identity_permission_or_tool(self):
        for key in ('task_id', 'step_id', 'operation_id', 'allowed_products', 'grant', 'path', 'tool'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                bind_tool_proposal(raw(**{key: 'invented'}), host())
        self.assertEqual(self.tool.inventory('widget')['available'], 10)

    def test_duplicate_keys_rejected_even_when_values_equal(self):
        value = raw().decode()
        for suffix in ('"quantity":4', '"decision":"reserve"', '"quantity":1'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                bind_tool_proposal((value[:-1] + ',' + suffix + '}').encode(), host())

    def test_exact_fields_version_and_object_required(self):
        missing = json.loads(raw()); del missing['reason']
        for value in (b'null', b'[]', b'"text"', json.dumps(missing).encode(),
                      raw(schema_version='document-stream-proposal/1.0')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bind_tool_proposal(value, host())

    def test_no_numeric_coercion_or_nonfinite_values(self):
        for field in ('quantity', 'expected_version'):
            for value in (True, False, 1.0, '1', None, float('nan'), float('inf'), -1, 2**31):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    bind_tool_proposal(raw(**{field: value}), host())
        with self.assertRaises(ValueError): bind_tool_proposal(raw(quantity=0), host())

    def test_malformed_encoding_size_and_depth_rejected(self):
        for value in (raw().decode(), b'\xff', b'{} trailing', b' '*16385, b'['*1500+b']'*1500):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                bind_tool_proposal(value, host())

    def test_host_scope_restricts_product_kind_and_per_step_quantity(self):
        for value, step in ((raw(product='other'), host()), (raw(quantity=6), host()),
                            (raw(), host(allowed_kinds=('release',))),
                            (raw(decision='shell'), host()),
                            (raw(reservation_id='invented'), host()),
                            (raw(reason='insufficient_stock'), host())):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bind_tool_proposal(value, step)

    def test_release_requires_exact_host_visible_reservation(self):
        self.executor.execute(bind_tool_proposal(raw(), host()).operation)
        step = host(step_id='step-2', operation_id='op-2',
                    reservations=(ReservationRef('op-1', 'widget', 4),))
        release = raw(decision='release', expected_version=1, reservation_id='op-1')
        receipt = self.executor.execute(bind_tool_proposal(release, step).operation)
        self.assertEqual(receipt['status'], 'applied')
        self.assertEqual(self.tool.inventory('widget')['available'], 10)
        for changes in ({'reservation_id': 'foreign'}, {'quantity': 3}, {'product': 'other'}):
            fields = dict(decision='release', expected_version=1, reservation_id='op-1')
            fields.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bind_tool_proposal(raw(**fields), step)

    def test_abstention_is_no_effect_not_completion_or_recovery(self):
        self.journal.prepare(bind_tool_proposal(raw(), host()).operation)
        result = bind_tool_proposal(raw(decision='abstain', product=None, quantity=None,
                                       expected_version=None, reason='insufficient_stock'), host())
        self.assertIsNone(result.operation)
        self.assertEqual(result.reason, 'insufficient_stock')
        self.assertEqual(self.journal.pending('task-a')['state'], 'pending')
        self.assertEqual(self.tool.inventory('widget')['available'], 10)
        for changes in ({'quantity': 1}, {'product': 'widget'}, {'expected_version': 0},
                        {'reservation_id': 'op-1'}, {'reason': 'none'}, {'reason': 'custom'}):
            fields = dict(decision='abstain', product=None, quantity=None, expected_version=None,
                          reservation_id=None, reason='no_action')
            fields.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bind_tool_proposal(raw(**fields), host())

    def test_tool_retains_aggregate_quota_and_version_checks(self):
        stale = bind_tool_proposal(raw(expected_version=2), host())
        self.assertEqual(self.executor.execute(stale.operation)['code'], 'version_mismatch')
        good = bind_tool_proposal(raw(), host(step_id='step-2', operation_id='op-2'))
        self.assertEqual(self.executor.execute(good.operation)['status'], 'applied')
        excessive = bind_tool_proposal(raw(expected_version=1), host(step_id='step-3', operation_id='op-3'))
        self.assertEqual(self.executor.execute(excessive.operation)['code'], 'quota_exceeded')

    def test_same_host_step_cannot_accept_changed_payload_after_effect(self):
        self.executor.execute(bind_tool_proposal(raw(), host()).operation)
        with self.assertRaises(Conflict):
            self.executor.execute(bind_tool_proposal(raw(quantity=3), host()).operation)
        with self.assertRaises(Conflict):
            self.executor.execute(bind_tool_proposal(raw(), host(operation_id='replacement')).operation)
        self.assertEqual(self.tool.inventory('widget')['available'], 6)

    def test_visible_reference_does_not_override_tool_ownership(self):
        self.executor.execute(bind_tool_proposal(raw(), host()).operation)
        self.tool.authorize('task-b', 'widget', 7)
        step = host(task_id='task-b', step_id='release', operation_id='foreign-release',
                    reservations=(ReservationRef('op-1', 'widget', 4),))
        proposal = bind_tool_proposal(raw(decision='release', expected_version=1,
                                          reservation_id='op-1'), step)
        self.assertEqual(self.executor.execute(proposal.operation)['code'], 'reservation_mismatch')
        self.assertEqual(self.tool.inventory('widget')['available'], 6)

    def test_new_proposal_cannot_bypass_pending_intent(self):
        self.journal.prepare(bind_tool_proposal(raw(), host()).operation)
        candidate = bind_tool_proposal(raw(quantity=3), host(step_id='next', operation_id='new'))
        with self.assertRaises(Conflict): self.executor.execute(candidate.operation)
        self.assertEqual(self.journal.pending('task-a')['id'], 'op-1')
        self.assertEqual(self.tool.inventory('widget')['available'], 10)

    def test_invalid_host_envelopes_rejected_without_mutable_permissions(self):
        for fields in ({'max_quantity':True}, {'max_quantity':0}, {'task_id':''},
                       {'allowed_products':['widget']}, {'allowed_products':('widget','widget')},
                       {'allowed_kinds':('shell',)}, {'allowed_kinds':()},
                       {'reservations':(ReservationRef('r', 'other', 2),)},
                       {'reservations':(ReservationRef('r', 'widget', 2), ReservationRef('r', 'widget', 3))}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): host(**fields)
        with self.assertRaises(ValueError): bind_tool_proposal(raw(), {'task_id':'task-a'})

    def test_schema_is_fixed_fresh_and_contains_no_authority_fields(self):
        first = tool_response_format()
        schema = first['json_schema']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']), set(json.loads(raw())))
        self.assertEqual(set(schema['required']), set(json.loads(raw())))
        schema['properties']['task_id'] = {'type':'string'}
        self.assertNotIn('task_id', tool_response_format()['json_schema']['schema']['properties'])


if __name__ == '__main__': unittest.main()
