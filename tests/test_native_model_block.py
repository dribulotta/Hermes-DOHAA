"""Synthetic provider/worker traces; real protected profiles and lease storage."""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

from hermes_dohaa.learning import collection,native_prompt as n
from hermes_dohaa.learning.native_reasoning import binary_model_sha256
from hermes_dohaa.learning.native_response_format import (response_format_for_policy,
    BOOLEAN_RESPONSE_CONTRACT, PROMPT_RESPONSE_CONTRACT, fixed_shadow_result_fields)
from tools import native_model_block as block
from tools.document_stream_request import make_request,native_request
from test_native_shadow_adapter import policy,wire,sse,encode,digest

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'protected Linux collector')
class NativeBlockTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.p=dict(policy(),schema_version='hermes-native-shadow-policy/1.2',context_length=8192,
                    response_contract='document-stream-proposal/1.0',request_limit=4)
        pb=encode(self.p);cb=encode(collection.create_collection_policy(adapter_sha256=n.native_adapter_sha256(),
                  runtime_policy_sha256=digest(pb),result_fields={'result':'object'}))
        self.adapter=n.NativePromptAdapter(policy_bytes=pb,expected_policy_sha256=digest(pb),collection_policy_bytes=cb,
            native_source=self.root,python=Path(sys.executable),evidence_dir=self.root/'native',api_key='synthetic')
        task=dict(task_id='block-synthetic',instruction='Report only facts supported by the observation.',fact_keys=['shade'])
        req=make_request(dict(run_id='block-isolation-control',at_tick=0,events=[]),task)
        self.requests=tuple(native_request(req,digest(cb),digest(f'block-new-{i}'.encode())) for i in range(2))
        self.instances=[];self.loads=0;self.unloads=0;self.sends=0;self.profiles=[];self.change=lambda trace,index:None
        self.after_call=lambda index:None;self.finish='stop';self.clock=0
        self.content=encode(dict(result=dict(facts=[]),claims=[],evidence=[],requested_actions=[])).decode()
        self.addCleanup(patch.stopall)
        def start(adapter):
            if self.instances:raise n.NativePromptError('native_shadow.precondition_failed')
            adapter.evidence_dir.mkdir(mode=0o700)
            adapter.worker_root=self.root/'workers';adapter.worker_root.mkdir(mode=0o755);adapter.state='idle'
        patch.object(n.NativePromptAdapter,'start',start).start()
        self.adapter._http=self.http
        self.model_sha=binary_model_sha256(encode(self.catalog()),self.p['model'])

    def catalog(self):
        return dict(models=[dict(key=self.p['model'],type='llm',max_context_length=8192,
            capabilities=dict(reasoning=dict(allowed_options=['off','on'],default='on')),
            loaded_instances=copy.deepcopy(self.instances))])

    def http(self,url,body=None):
        if url.endswith('/load'):
            self.loads+=1;self.instances[:]=[dict(id='block-owned',config=dict(context_length=8192))]
            return dict(status='loaded',instance_id='block-owned',load_config=dict(context_length=8192))
        if url.endswith('/unload'):
            self.assertEqual(body,dict(instance_id='block-owned'));self.unloads+=1;self.instances.clear()
            return dict(status='unloaded')
        return self.catalog()

    def spawn(self,args,**kw):
        proc=Mock(returncode=0)
        def communicate(data,timeout):
            config=json.loads(data);logical=config['request'];index=self.sends;self.sends+=1
            profile=Path(config['profile']);self.profiles.append(profile)
            self.assertEqual(list(profile.iterdir()),[])
            if index:self.assertEqual(self.profiles[index-1].stat().st_uid,0)
            (profile/'private-marker').write_text(f'only-{index}')
            wr=wire(logical,self.p);wr['response_format']=response_format_for_policy(self.p)
            trace=dict(schema_version='hermes-shadow-trace/1.0',request_sha256=digest(encode(logical)),
                runtime_policy_sha256=self.adapter.runtime_policy_sha256,bridge_sha256=self.p['bridge_sha256'],
                uid=self.p['worker_uid'],gid=self.p['worker_gid'],identity_isolated=True,agent_class='AIAgent',
                profile_passed=True,server_finished=True,native_completed=True,content_matches_wire=True,
                actual_requests=1,denied_continuations=0,wire_request_base64=base64.b64encode(encode(wr)).decode(),
                wire_response_base64=base64.b64encode(sse(self.content,self.finish)).decode())
            self.change(trace,index);kw['stdout'].write(encode(trace));self.after_call(index)
        proc.communicate.side_effect=communicate
        return proc

    def run_block(self,requests=None,**kwargs):
        with patch.object(n.subprocess,'Popen',side_effect=self.spawn):
            return block.record_block(self.root,self.adapter,self.requests if requests is None else requests,self.model_sha,
                                      wall_seconds=60,no_new_call_margin_seconds=5,**kwargs)

    def configure_shadow(self, contract, count, fields=None):
        self.p.update(response_contract=contract, request_limit=8)
        pb=encode(self.p)
        cb=encode(collection.create_collection_policy(adapter_sha256=n.native_adapter_sha256(),
            runtime_policy_sha256=digest(pb), result_fields=fields or fixed_shadow_result_fields(contract)))
        self.adapter=n.NativePromptAdapter(policy_bytes=pb,expected_policy_sha256=digest(pb),
            collection_policy_bytes=cb,native_source=self.root,python=Path(sys.executable),
            evidence_dir=self.root/'native',api_key='synthetic')
        self.adapter._http=self.http
        self.requests=tuple(collection._request('fixed-shadow-block',i,
            {'input_sha256':digest(f'New synthetic public input {i}'.encode())},
            f'New synthetic public input {i}', 'Fixed synthetic shadow prompt',digest(cb)) for i in range(count))

    def test_eight_boolean_observations_share_one_load_and_exact_unload(self):
        self.configure_shadow(BOOLEAN_RESPONSE_CONTRACT,8)
        self.content=encode(dict(result=dict(answer=True),actions=[])).decode()
        result=self.run_block()
        self.assertEqual(result['status'],'completed')
        self.assertEqual((self.loads,self.sends,self.unloads),(1,8,1))
        self.assertEqual(len(set(self.profiles)),8)
        self.assertTrue(all(json.loads((self.root/'calls'/f'{i:03d}'/'receipt.private.json').read_bytes())['content']==self.content for i in range(8)))

    def test_one_prompt_proposal_uses_existing_block_lifecycle(self):
        self.configure_shadow(PROMPT_RESPONSE_CONTRACT,1)
        self.content=encode(dict(result=dict(artifact='New synthetic candidate',rationale='Synthetic explanation'),actions=[])).decode()
        result=self.run_block()
        self.assertEqual(result['status'],'completed')
        self.assertEqual((self.loads,self.sends,self.unloads),(1,1,1))

    def test_mismatched_shadow_collection_fields_fail_before_load(self):
        self.configure_shadow(BOOLEAN_RESPONSE_CONTRACT,1,fields={'artifact':'string','rationale':'string'})
        with self.assertRaisesRegex(ValueError,'contracts differ'):
            self.run_block()
        self.assertEqual((self.loads,self.sends,self.unloads),(0,0,0))
        self.assertFalse((self.root/'started.private.json').exists())

    def test_unknown_boolean_completion_preserves_unresolved_lease_without_cleanup(self):
        self.configure_shadow(BOOLEAN_RESPONSE_CONTRACT,2)
        self.content=encode(dict(result=dict(answer=True),actions=[])).decode()
        self.change=lambda t,i:t.update(server_finished=False) if i==0 else None
        result=self.run_block()
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual((self.loads,self.sends,self.unloads),(1,1,0))
        self.assertEqual(result['residency_state'],'generating')

    def test_existing_document_collection_with_all_envelope_fields_is_preserved(self):
        self.configure_shadow('document-stream-proposal/1.0',2,fields={
            'result':'object','claims':'array','evidence':'array','requested_actions':'array'})
        result=self.run_block()
        self.assertEqual(result['status'],'completed')
        self.assertEqual((self.loads,self.sends,self.unloads),(1,2,1))

    def test_two_requests_share_one_load_and_one_exact_unload_with_fresh_profiles(self):
        result=self.run_block()
        self.assertEqual((self.loads,self.sends,self.unloads),(1,2,1));self.assertEqual(self.instances,[])
        self.assertEqual(result['status'],'completed');self.assertTrue(result['exact_unload'])
        self.assertEqual(result['verified_requests'],2);self.assertEqual(result['unattempted_requests'],0)
        self.assertEqual([x['resident_position'] for x in result['calls']],['first','subsequent'])
        self.assertTrue(all(x['usage'] is None for x in result['calls']))
        self.assertEqual(len(set(self.profiles)),2)
        for i,profile in enumerate(self.profiles):
            n.protected_directory(profile)
            witness=json.loads((self.root/'calls'/f'{i:03d}'/'witness.private.json').read_bytes())
            self.assertEqual(witness['profile'],str(profile));self.assertEqual(witness['worker_index'],i)
        with sqlite3.connect(self.root/'residency.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM terminals').fetchone()[0],2)

    def test_duplicate_or_changed_request_is_rejected_before_load(self):
        for requests in ((self.requests[0],self.requests[0]),(self.requests[0],b'{}')):
            with self.assertRaises((ValueError,n.NativePromptError)):self.run_block(requests)
        self.assertEqual((self.loads,self.sends,self.unloads),(0,0,0))

    def test_request_id_reuse_with_different_input_is_rejected(self):
        value=json.loads(self.requests[1]);value['request_id']=json.loads(self.requests[0])['request_id']
        with self.assertRaises(ValueError):self.run_block((self.requests[0],encode(value)))
        self.assertEqual(self.loads,0)

    def test_empty_and_over_budget_blocks_never_load(self):
        for requests in ((),self.requests*3):
            with self.assertRaises(ValueError):self.run_block(requests)
        self.assertEqual(self.loads,0)

    def test_unknown_second_completion_stops_without_resend_or_blind_unload(self):
        self.change=lambda t,i:t.update(server_finished=False) if i==1 else None
        result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertFalse(result['exact_unload'])
        self.assertEqual((self.loads,self.sends,self.unloads),(1,2,0))
        self.assertEqual(result['residency_state'],'generating');self.assertEqual(result['verified_requests'],1)
        self.assertEqual(result['attempted_requests'],2)

    def test_known_budget_terminal_is_retained_and_next_request_runs(self):
        def change(trace,index):
            if index==0:trace['wire_response_base64']=base64.b64encode(sse(self.content,'length')).decode()
        self.change=change;result=self.run_block()
        self.assertEqual(result['status'],'completed');self.assertEqual(self.sends,2);self.assertEqual(self.unloads,1)
        self.assertEqual(result['calls'][0]['error_code'],'budget_exhausted')

    def test_optional_accounting_occurs_after_unload(self):
        def fail(*args):
            self.assertEqual(self.unloads,1);raise ValueError('synthetic accounting failure')
        with patch.object(block,'usage',side_effect=fail):result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertTrue(result['exact_unload'])
        self.assertEqual((self.loads,self.sends,self.unloads),(1,2,1))

    def test_wall_budget_between_known_calls_stops_and_unloads(self):
        self.after_call=lambda index:setattr(self,'clock',56_000_000_000)
        with patch.object(block.time,'monotonic_ns',side_effect=lambda:self.clock):result=self.run_block()
        self.assertEqual((self.sends,self.unloads),(1,1));self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['unattempted_requests'],1);self.assertEqual(result['stop_reason'],'wall_budget')

    def test_catalog_identity_mismatch_prevents_load(self):
        self.model_sha='f'*64;result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertEqual((self.loads,self.sends),(0,0))

    def test_existing_unowned_model_is_never_unloaded(self):
        self.instances[:]=[dict(id='not-owned',config=dict(context_length=8192))]
        result=self.run_block();self.assertEqual(result['status'],'incomplete')
        self.assertEqual((self.loads,self.sends,self.unloads),(0,0,0))

    def test_closed_block_cannot_be_replayed(self):
        self.run_block()
        with self.assertRaises((ValueError,n.NativePromptError,FileExistsError)):self.run_block()
        self.assertEqual((self.loads,self.sends,self.unloads),(1,2,1))

    def test_binding_tamper_keeps_unknown_terminal_owned_and_stops(self):
        self.change=lambda t,i:t.update(request_sha256='e'*64) if i==0 else None
        result=self.run_block();self.assertEqual(result['status'],'incomplete')
        self.assertEqual((self.sends,self.unloads),(1,0));self.assertEqual(result['residency_state'],'generating')

    def test_another_instance_appearing_between_calls_is_not_unloaded(self):
        self.after_call=lambda i:self.instances.append(dict(id='foreign',config=dict(context_length=8192))) if i==0 else None
        result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertEqual((self.sends,self.unloads),(1,0))
        self.assertEqual(result['verified_requests'],1);self.assertEqual(result['residency_state'],'ready')

    def test_unknown_unload_result_is_not_retried(self):
        previous=self.adapter._http
        def http(url,body=None):
            if url.endswith('/unload'):
                self.unloads+=1;raise TimeoutError('synthetic unload timeout')
            return previous(url,body)
        self.adapter._http=http;result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertFalse(result['exact_unload'])
        self.assertEqual((self.sends,self.unloads),(2,1));self.assertEqual(result['residency_state'],'unloading')

    def test_storage_failure_after_known_terminal_closes_own_model_and_stops(self):
        previous=block._save
        def save(path,value):
            if path.name=='witness.private.json':raise OSError('synthetic witness storage failure')
            previous(path,value)
        with patch.object(block,'_save',side_effect=save):result=self.run_block()
        self.assertEqual(result['status'],'incomplete');self.assertTrue(result['exact_unload'])
        self.assertEqual((self.sends,self.unloads),(1,1));self.assertEqual(result['residency_state'],'closed')
