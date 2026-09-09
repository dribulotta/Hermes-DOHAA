"""Process-crash and concurrency tests for the synthetic tool boundary."""
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.controlled_tools import (Conflict, PendingIntent, Operation, ToolStore,
                                    IntentJournal, Executor)


def reserve(**changes):
    fields=dict(operation_id='host-op-1',task_id='task-a',step_id='reserve-1',
                kind='reserve',product='synthetic-widget',quantity=4,expected_version=0)
    fields.update(changes)
    return Operation(**fields)


def stores(root):
    return ToolStore(root/'tool.sqlite3'), IntentJournal(root/'intents.sqlite3')


class ControlledToolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.tool,self.journal=stores(self.root)
        self.tool.seed('synthetic-widget',10)
        self.tool.authorize('task-a','synthetic-widget',7)
        self.executor=Executor(self.journal,self.tool)

    def test_retry_recovers_original_receipt_without_another_effect(self):
        first=self.executor.execute(reserve())
        reopened_tool,reopened_journal=stores(self.root)
        self.assertEqual(Executor(reopened_journal,reopened_tool).execute(reserve()),first)
        self.assertEqual(reopened_tool.inventory('synthetic-widget'),{'available':6,'version':1})

    def test_same_id_changed_payload_or_same_step_new_id_rejected(self):
        self.executor.execute(reserve())
        for op in (reserve(quantity=3),reserve(operation_id='new-id')):
            with self.subTest(op=op),self.assertRaises(Conflict):self.executor.execute(op)
            with self.assertRaises(Conflict):self.tool.apply(op)
        self.assertEqual(self.tool.inventory('synthetic-widget')['available'],6)

    def test_unresolved_intent_blocks_a_different_proposal(self):
        self.journal.prepare(reserve())
        with self.assertRaises(PendingIntent):
            self.executor.execute(reserve(operation_id='new',step_id='next'))
        self.assertEqual(self.tool.inventory('synthetic-widget')['available'],10)

    def test_receipt_lookup_failure_keeps_unknown_and_does_not_apply(self):
        self.journal.prepare(reserve())
        with (patch.object(self.tool,'lookup',side_effect=OSError('unavailable')),
              patch.object(self.tool,'apply') as apply):
            with self.assertRaises(OSError):self.executor.recover('task-a')
        apply.assert_not_called()
        self.assertEqual(self.journal.pending('task-a')['state'],'pending')

    def test_rejected_precondition_is_durable_not_reinterpreted(self):
        op=reserve(expected_version=1)
        first=self.executor.execute(op)
        self.assertEqual(first['code'],'version_mismatch')
        self.tool.apply(reserve(operation_id='other',task_id='task-a',step_id='other'))
        self.assertEqual(self.executor.execute(op),first)

    def test_authorization_is_per_task_and_aggregate_active_quantity(self):
        self.assertEqual(self.tool.apply(reserve(task_id='unauthorized',operation_id='unauthorized-op'))['code'],'not_authorized')
        self.assertEqual(self.executor.execute(reserve())['status'],'applied')
        second=reserve(operation_id='second',step_id='second',expected_version=1)
        self.assertEqual(self.executor.execute(second)['code'],'quota_exceeded')
        with self.assertRaises(Conflict):self.tool.authorize('task-a','synthetic-widget',100)

    def test_release_owner_binding_and_no_double_release(self):
        self.executor.execute(reserve())
        def release(**kwargs):
            return reserve(operation_id='release-1',step_id='release-1',kind='release',
                           expected_version=1,reservation_id='host-op-1',**kwargs)
        self.tool.authorize('task-b','synthetic-widget',7)
        self.assertEqual(self.tool.apply(dataclasses.replace(release(task_id='task-b'),operation_id='foreign-release'))['code'],'reservation_mismatch')
        receipt=self.executor.execute(release())
        self.assertEqual(receipt['status'],'applied')
        self.assertEqual(self.executor.execute(release()),receipt)
        second=dataclasses.replace(release(),operation_id='release-2',step_id='release-2',expected_version=2)
        self.assertEqual(self.executor.execute(second)['code'],'reservation_inactive')
        self.assertEqual(self.tool.inventory('synthetic-widget'),{'available':10,'version':2})

    def test_concurrent_reservations_cannot_overdraw(self):
        self.tool.authorize('task-b','synthetic-widget',7)
        ops=[reserve(quantity=7),reserve(operation_id='other',task_id='task-b',quantity=7)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts=list(pool.map(self.tool.apply,ops))
        self.assertEqual(sum(r['status']=='applied' for r in receipts),1)
        self.assertEqual(self.tool.inventory('synthetic-widget'),{'available':3,'version':1})

    def test_concurrent_duplicate_has_one_effect(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts=list(pool.map(lambda _:self.executor.execute(reserve()),range(4)))
        self.assertTrue(all(r==receipts[0] for r in receipts))
        self.assertEqual(self.tool.inventory('synthetic-widget')['available'],6)

    def test_crashes_and_reopen_preserve_effect_and_intent_boundaries(self):
        stages=('before_intent','after_intent','after_effect_before_receipt','after_effect','after_receipt','after_complete')
        for stage in stages:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                tool,journal=stores(root)
                tool.seed('synthetic-widget',10);tool.authorize('task-a','synthetic-widget',7)
                child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--crash',str(root),stage],
                                     capture_output=True,timeout=15)
                self.assertEqual(child.returncode,73,child.stderr.decode(errors='replace'))
                tool,journal=stores(root)
                committed=stage in ('after_effect','after_receipt','after_complete')
                self.assertEqual(tool.inventory('synthetic-widget')['available'],6 if committed else 10)
                self.assertEqual(tool.lookup('host-op-1') is not None,committed)
                if stage=='before_intent':self.assertIsNone(journal.pending('task-a'))
                elif stage!='after_complete':self.assertIsNotNone(journal.pending('task-a'))
                executor=Executor(journal,tool)
                receipt=executor.execute(reserve()) if stage=='before_intent' else executor.recover('task-a')
                if stage=='after_complete':receipt=executor.execute(reserve())
                self.assertEqual(receipt['status'],'applied')
                self.assertIsNone(journal.pending('task-a'))
                self.assertEqual(tool.inventory('synthetic-widget'),{'available':6,'version':1})

    def test_bad_receipt_cannot_mark_intent_complete(self):
        self.journal.prepare(reserve())
        receipt=self.tool.apply(reserve())
        with self.assertRaises(Conflict):
            self.journal.record_receipt('host-op-1',dict(receipt,request_sha256='0'*64))
        self.assertEqual(self.journal.pending('task-a')['state'],'pending')

    def test_invalid_quantities_and_versions_never_enter_journal(self):
        for changes in ({'quantity':True},{'quantity':0},{'quantity':-1},{'expected_version':True},
                        {'expected_version':-1},{'kind':'shell'},{'kind':'release'}, {'task_id':''}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):reserve(**changes)
        self.assertIsNone(self.journal.pending('task-a'))

    def test_malformed_or_changed_receipts_are_not_completion(self):
        self.journal.prepare(reserve())
        receipt=self.tool.apply(reserve())
        for changes in ({'version':True},{'available':-1},{'code':'invented'},
                        {'status':'rejected','code':'applied'},{'available':None}):
            with self.subTest(changes=changes),self.assertRaises(Conflict):
                self.journal.record_receipt('host-op-1',dict(receipt,**changes))
        with self.assertRaises(PendingIntent):self.journal.complete('host-op-1')
        self.journal.record_receipt('host-op-1',receipt)
        with self.assertRaises(Conflict):
            self.journal.record_receipt('host-op-1',dict(receipt,available=5))

    def test_insufficient_stock_and_unknown_product_make_no_effect(self):
        self.tool.authorize('task-rich','synthetic-widget',20)
        self.assertEqual(self.tool.apply(reserve(task_id='task-rich',quantity=11))['code'],'insufficient_stock')
        self.tool.authorize('task-a','missing',7)
        self.assertEqual(self.tool.apply(reserve(operation_id='missing-op',step_id='missing',product='missing'))['code'],'unknown_product')
        self.assertEqual(self.tool.inventory('synthetic-widget'),{'available':10,'version':0})

    def test_databases_cannot_share_the_same_file(self):
        with self.assertRaises(ValueError):
            Executor(IntentJournal(self.tool.path),self.tool)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash':
        root,stage=Path(sys.argv[2]),sys.argv[3]
        def crash(at):
            if at==stage:os._exit(73)
        tool,journal=stores(root)
        tool.fault=crash
        Executor(journal,tool,fault=crash).execute(reserve())
        raise SystemExit(99)
    unittest.main()
