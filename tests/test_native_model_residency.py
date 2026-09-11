import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.native_model_residency import ModelResidency, ResidencyError
from hermes_dohaa.learning import native_worker as worker

POLICY='a'*64
REQUEST='b'*64


class Backend:
    def __init__(self, path):
        self.path=Path(path)
        self.loads=0;self.unloads=[]
    def catalog(self):
        instances=json.loads(self.path.read_text()) if self.path.exists() else []
        return {'models':[{'key':'synthetic-model','max_context_length':16384,'loaded_instances':instances}]}
    def load(self, request):
        self.loads+=1
        self.path.write_text(json.dumps([{'id':'owned-instance','config':{'context_length':request['context_length']}}]))
        return {'status':'loaded','instance_id':'owned-instance','load_config':{'context_length':request['context_length']}}
    def unload(self, request):
        self.unloads.append(request)
        self.path.write_text('[]')


def lease(root, backend, fault=None):
    return ModelResidency(root/'ownership.sqlite3',model='synthetic-model',context_length=8192,
        policy_sha256=POLICY,catalog=backend.catalog,load=backend.load,unload=backend.unload,fault=fault)


def terminal(request=REQUEST):
    return json.dumps({'schema_version':'hermes-shadow-terminal/1.0','request_sha256':request,
        'server_finished':True,'status':'completed','content':'synthetic'}).encode()


class ResidencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.backend=Backend(self.root/'backend.json')
        self.lease=lease(self.root,self.backend)

    def test_load_receipt_survives_reopen_and_exact_unload(self):
        self.lease.start()
        reopened=lease(self.root,self.backend)
        self.assertEqual(reopened.snapshot()['state'],'ready')
        self.assertEqual(reopened.snapshot()['instance_id'],'owned-instance')
        reopened.begin_generation(REQUEST);reopened.record_terminal(REQUEST,terminal())
        reopened.finish()
        self.assertEqual(self.backend.loads,1)
        self.assertEqual(self.backend.unloads,[{'instance_id':'owned-instance'}])
        self.assertEqual(reopened.snapshot()['state'],'closed')

    def test_lost_load_ack_never_reloads_or_infers_ownership(self):
        def lost(request):self.backend.load(request);raise TimeoutError('private')
        self.lease.load=lost
        with self.assertRaises(TimeoutError):self.lease.start()
        reopened=lease(self.root,self.backend)
        for action in (reopened.start,reopened.finish,lambda:reopened.begin_generation(REQUEST)):
            with self.assertRaises(ResidencyError):action()
        self.assertEqual(reopened.snapshot()['state'],'loading')
        self.assertIsNone(reopened.snapshot()['instance_id'])
        self.assertEqual(self.backend.loads,1);self.assertFalse(self.backend.unloads)

    def test_unknown_generation_blocks_cleanup_even_if_backend_empty(self):
        self.lease.start();self.lease.begin_generation(REQUEST)
        self.backend.path.write_text('[]')
        reopened=lease(self.root,self.backend)
        with self.assertRaises(ResidencyError):reopened.finish()
        with self.assertRaises(ResidencyError):reopened.record_terminal(REQUEST,b'{}')
        self.assertEqual(reopened.snapshot()['state'],'generating')
        self.assertFalse(self.backend.unloads)

    def test_terminal_must_match_outstanding_request(self):
        self.lease.start();self.lease.begin_generation(REQUEST)
        for req,raw in ((POLICY,terminal()),(REQUEST,terminal(POLICY)),(REQUEST,b'{"server_finished":true}')):
            with self.assertRaises(ResidencyError):self.lease.record_terminal(req,raw)
        self.assertEqual(self.lease.snapshot()['state'],'generating')
        self.lease.record_terminal(REQUEST,terminal())

    def test_context_mismatch_records_identity_but_prevents_generation(self):
        original=self.backend.load
        def wrong(request):return original(dict(request,context_length=4096))
        self.lease.load=wrong
        with self.assertRaises(ResidencyError):self.lease.start()
        self.assertEqual(self.lease.snapshot()['state'],'loaded_invalid')
        self.assertEqual(self.lease.snapshot()['instance_id'],'owned-instance')
        with self.assertRaises(ResidencyError):self.lease.begin_generation(REQUEST)
        self.lease.finish()
        self.assertEqual(self.backend.unloads,[{'instance_id':'owned-instance'}])

    def test_foreign_or_changed_residency_prevents_generation_and_unload(self):
        self.lease.start()
        for instances in ([{'id':'foreign','config':{'context_length':8192}}],
                          [{'id':'owned-instance','config':{'context_length':4096}}], []):
            self.backend.path.write_text(json.dumps(instances))
            with self.assertRaises(ResidencyError):self.lease.begin_generation(REQUEST)
            with self.assertRaises(ResidencyError):self.lease.finish()
        self.assertFalse(self.backend.unloads)

    def test_existing_model_blocks_load_and_duplicate_catalog_is_rejected(self):
        self.backend.load({'context_length':8192})
        with self.assertRaises(ResidencyError):self.lease.start()
        self.assertEqual(self.lease.snapshot()['state'],'new')
        self.backend.path.write_text('[]')
        self.lease.catalog=lambda:{'models':[self.backend.catalog()['models'][0]]*2}
        with self.assertRaises(ResidencyError):self.lease.start()
        self.assertEqual(self.backend.loads,1)

    def test_changed_policy_cannot_reopen_ownership(self):
        self.lease.start()
        with self.assertRaises(ResidencyError):
            ModelResidency(self.root/'ownership.sqlite3',model='synthetic-model',context_length=8192,
                policy_sha256='c'*64,catalog=self.backend.catalog,load=self.backend.load,unload=self.backend.unload)

    def test_unknown_unload_is_never_reissued(self):
        self.lease.start()
        def lost(request):self.backend.unload(request);raise TimeoutError()
        self.lease.unload=lost
        with self.assertRaises(TimeoutError):self.lease.finish()
        reopened=lease(self.root,self.backend)
        with self.assertRaises(ResidencyError):reopened.finish()
        self.assertEqual(reopened.snapshot()['state'],'unloading')
        self.assertEqual(len(self.backend.unloads),1)

    def test_missing_load_identity_stays_unknown(self):
        self.lease.load=lambda request:{'status':'loaded','load_config':{'context_length':8192}}
        with self.assertRaises(ResidencyError):self.lease.start()
        self.assertEqual(self.lease.snapshot()['state'],'loading')
        with self.assertRaises(ResidencyError):self.lease.finish()

    def test_process_crash_after_load_receipt_retains_cleanup_authority(self):
        child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--crash',str(self.root)],
                             capture_output=True,timeout=15)
        self.assertEqual(child.returncode,73,child.stderr.decode(errors='replace'))
        reopened=lease(self.root,self.backend)
        self.assertEqual(reopened.snapshot()['state'],'ready')
        reopened.finish()
        self.assertEqual(self.backend.unloads,[{'instance_id':'owned-instance'}])

    def test_zero_dispatch_setup_failure_still_cannot_authorize_terminal(self):
        self.lease.start();self.lease.begin_generation(REQUEST)
        with self.assertRaises(ResidencyError):
            self.lease.record_terminal(REQUEST,json.dumps({'actual_requests':0,'profile_passed':False}).encode())
        with self.assertRaises(ResidencyError):self.lease.finish()

    def test_malformed_terminal_content_does_not_release_generation(self):
        self.lease.start();self.lease.begin_generation(REQUEST)
        for change in ({'content':None},{'status':'failed','error_code':'unknown'}, {'extra':'unbound'}):
            raw=json.loads(terminal());raw.update(change)
            with self.assertRaises(ResidencyError):self.lease.record_terminal(REQUEST,json.dumps(raw).encode())
        self.assertEqual(self.lease.snapshot()['state'],'generating')

    def test_terminal_transition_rechecks_request_inside_transaction(self):
        self.lease.start();self.lease.begin_generation(REQUEST)
        snapshot=self.lease.snapshot()
        with self.lease._transaction() as db:
            db.execute('UPDATE residency SET request_sha=?',('c'*64,))
        with patch.object(self.lease,'snapshot',return_value=snapshot):
            with self.assertRaises(ResidencyError):self.lease.record_terminal(REQUEST,terminal())
        self.assertEqual(self.lease.snapshot()['request_sha'],'c'*64)


class SetupDiagnosticsTests(unittest.TestCase):
    def test_stage_and_exception_bucket_without_private_message(self):
        for error,bucket in ((PermissionError('private path'),'permission'),(ValueError('private input'),'value'),
                             (TimeoutError('private host'),'timeout'),(RuntimeError('private secret'),'runtime')):
            trace={}
            with self.assertRaises(type(error)):
                with worker.setup_phase(trace,'create_agent'):raise error
            self.assertEqual(trace['setup_failure'],{'stage':'create_agent','exception_kind':bucket})
            self.assertNotIn('private',json.dumps(trace))

    def test_arbitrary_exception_names_and_stages_do_not_escape(self):
        SecretNamedException=type('CredentialSecretException',(Exception,),{})
        trace={}
        with self.assertRaises(SecretNamedException):
            with worker.setup_phase(trace,'profile_check'):raise SecretNamedException('secret')
        self.assertEqual(trace['setup_failure']['exception_kind'],'other')
        with self.assertRaises(ValueError):
            with worker.setup_phase({},'untrusted stage'):pass

    def test_success_has_no_diagnostic_and_first_failure_is_retained(self):
        trace={}
        with worker.setup_phase(trace,'configure_agent'):pass
        self.assertFalse(trace)
        for stage in ('create_agent','profile_check'):
            with self.assertRaises(ValueError):
                with worker.setup_phase(trace,stage):raise ValueError('secret')
        self.assertEqual(trace['setup_failure']['stage'],'create_agent')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash':
        root=Path(sys.argv[2]);backend=Backend(root/'backend.json')
        def crash(stage):
            if stage=='load_receipt_committed':os._exit(73)
        lease(root,backend,fault=crash).start()
        raise SystemExit(99)
    unittest.main()
