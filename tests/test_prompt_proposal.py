import base64
import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.learning import collection as c, proposal as p, shadow
from hermes_dohaa.learning.artifacts import load_artifact_snapshot

encode, digest = shadow._canonical, shadow._hash


class ProposalFixtureAdapter:
    """Synthetic terminal receipts only; no model, subprocess, network or actions."""
    runtime_policy_sha256 = digest(b'fixed synthetic proposal runtime')

    def __init__(self, directory):
        self.directory = directory
        self.mode = 'valid'
        self.starts = self.finishes = 0
        self.requests = []
        self.content = None

    def start(self):
        self.starts += 1
        return encode(dict(c._LIFECYCLE, idle=False)) if self.mode == 'bad-start' else encode(c._LIFECYCLE)

    def finish(self):
        self.finishes += 1
        if self.mode == 'bad-finish':
            raise RuntimeError('SECRET provider credential')
        return encode(c._LIFECYCLE)

    def generate(self, raw):
        self.requests.append(raw)
        assert json.loads((self.directory/'0002.json').read_bytes())['payload']['request'].encode() == raw
        request = json.loads(raw)
        if self.mode == 'raise':
            raise RuntimeError('SECRET provider host')
        if self.mode == 'interrupt':
            raise KeyboardInterrupt()
        if self.mode == 'forged-error':
            raise shadow.ShadowError('SECRET forged error code')
        if self.mode == 'oversized-receipt':
            return b'x'*(8*c.MAX_TEXT_BYTES+1)
        if self.mode == 'invalid-utf8':
            return b'\xff\xfe'
        content = self.content or json.dumps({'result': {'artifact': 'PRIVATE proposed prompt',
            'rationale': 'PRIVATE synthetic rationale'}, 'actions': []})
        receipt = {'schema_version': 'hermes-shadow-terminal/1.0', 'request_sha256': digest(raw),
            'server_finished': self.mode != 'uncertain', 'status': 'completed', 'content': content}
        if self.mode == 'wrong-request':
            receipt['request_sha256'] = 'a'*64
        if self.mode == 'budget':
            receipt.pop('content'); receipt.update(status='failed', error_code='budget_exhausted')
        if self.mode == 'oversized-content':
            receipt['content'] = 'x'*(c.MAX_TEXT_BYTES+1)
        return encode(receipt)


def fixture(adapter):
    baseline = b'PRIVATE baseline prompt'
    rows = []
    for text, response, codes in [('Public training one', 'Observed incorrect answer', ['result.incorrect']),
                                  ('Public training two', '', ['runtime.budget_exhausted'])]:
        rows.append({'input': text, 'input_sha256': digest(text.encode()), 'baseline_response': response,
                     'response_sha256': digest(response.encode()), 'feedback_codes': codes})
    training = encode({'schema_version': 'hermes-training-projection/1.0', 'baseline_sha256': digest(baseline), 'records': rows})
    split = encode({'schema_version': 'hermes-training-partition/1.0',
        'training_input_sha256': [r['input_sha256'] for r in rows],
        'heldout_input_sha256': [digest(b'PRIVATE HELDOUT case never give to generator')]})
    wire_policy = encode(c.create_collection_policy(adapter_sha256=c.adapter_source_sha256(adapter),
        runtime_policy_sha256=adapter.runtime_policy_sha256, result_fields=p.FIELDS))
    args = dict(baseline_bytes=baseline, training_bytes=training, split_bytes=split, wire_policy_bytes=wire_policy)
    plan = encode(p.create_prompt_proposal_plan(**args))
    return dict(args, plan_bytes=plan, expected_plan_sha256=digest(plan))


def rechain(data):
    previous = '0'*64
    for index, event in enumerate(data['events']):
        event['sequence'], event['previous_sha256'] = index, previous
        previous = digest(encode(event))
    return encode(data)


@unittest.skipUnless(os.name == 'posix', 'private publication requires POSIX')
class PromptProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root/'proposal'
        self.adapter = ProposalFixtureAdapter(self.directory)
        self.args = fixture(self.adapter)

    def tearDown(self):
        self.temporary.cleanup()

    def run_proposal(self):
        return p.propose_prompt_candidate(**self.args, adapter=self.adapter, output_dir=self.directory)

    def audit_args(self):
        recording = (self.directory/'recording.json').read_bytes()
        candidate = self.directory/'candidate.json'
        return dict(self.args, recording_bytes=recording, expected_recording_sha256=digest(recording),
                    candidate_bytes=candidate.read_bytes() if candidate.exists() else None)

    def test_one_durable_dispatch_terminal_unload_and_independent_candidate_audit(self):
        report = self.run_proposal()
        self.assertEqual(report['status'], 'candidate_recorded')
        self.assertEqual(report, p.audit_prompt_proposal(**self.audit_args()))
        self.assertEqual((self.adapter.starts,len(self.adapter.requests),self.adapter.finishes), (1,1,1))
        self.assertEqual(len(list(self.directory.glob('000?.json'))), 5)
        snapshot = load_artifact_snapshot(self.directory/'candidate.json', expected_id=report['candidate_id'],
                                          artifact_dir=self.directory/'artifacts')
        self.assertEqual(snapshot.baseline.content, self.args['baseline_bytes'])
        self.assertEqual([x.content for x in snapshot.evidence],
                         [self.args['training_bytes'], (self.directory/'recording.json').read_bytes()])
        self.assertEqual(snapshot.candidate.to_dict()['candidate']['artifact'], 'PRIVATE proposed prompt')

    def test_generator_receives_only_exact_training_projection_and_baseline(self):
        self.run_proposal()
        request = json.loads(self.adapter.requests[0])
        task = json.loads(request['input'])['task']
        self.assertEqual(set(task), {'baseline_prompt','training_observations'})
        expected = [{k:v for k,v in row.items() if k in {'input','baseline_response','feedback_codes'}}
                    for row in json.loads(self.args['training_bytes'])['records']]
        self.assertEqual(task['training_observations'], expected)
        self.assertEqual(task['baseline_prompt'].encode(), self.args['baseline_bytes'])
        self.assertEqual(request['prompt'], p.GENERATOR_PROMPT)
        wire = self.adapter.requests[0].decode()
        for forbidden in ('PRIVATE HELDOUT','heldout_input_sha256','expected_result','evidence_sha256',
                          digest(b'PRIVATE HELDOUT case never give to generator')):
            self.assertNotIn(forbidden, wire)

    def test_holdout_overlap_or_missing_training_partition_rejected_before_call(self):
        original = json.loads(self.args['split_bytes'])
        for change in ('overlap','missing','duplicate','empty'):
            split = copy.deepcopy(original)
            if change == 'overlap': split['heldout_input_sha256'] = [split['training_input_sha256'][0]]
            if change == 'missing': split['training_input_sha256'].pop()
            if change == 'duplicate': split['heldout_input_sha256'] *= 2
            if change == 'empty': split['heldout_input_sha256'] = []
            with self.subTest(change=change), self.assertRaises(shadow.ShadowError):
                p.propose_prompt_candidate(**dict(self.args, split_bytes=encode(split)), adapter=self.adapter, output_dir=self.directory)
        self.assertEqual(self.adapter.starts, 0); self.assertFalse(self.directory.exists())

    def test_extra_oracle_values_or_freeform_feedback_fields_rejected(self):
        training = json.loads(self.args['training_bytes'])
        for location, key in [('root','heldout_answers'), ('row','expected_result'), ('row','reason'), ('row','approval')]:
            value = copy.deepcopy(training)
            (value if location == 'root' else value['records'][0])[key] = 'PRIVATE protected oracle'
            with self.subTest(key=key), self.assertRaises(shadow.ShadowError):
                p.create_prompt_proposal_plan(**{k:v for k,v in dict(self.args, training_bytes=encode(value)).items()
                    if k not in {'plan_bytes','expected_plan_sha256'}})
        self.assertEqual(self.adapter.requests, [])

    def test_unknown_duplicate_conflicting_or_structured_feedback_rejected(self):
        for codes in (['Expected answer is 123'], ['result.incorrect']*2,
                      ['result.correct','result.incorrect'], [{'reason':'PRIVATE oracle'}], []):
            training = json.loads(self.args['training_bytes']); training['records'][0]['feedback_codes'] = codes
            with self.subTest(codes=codes), self.assertRaises(shadow.ShadowError):
                p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(training)),
                    adapter=self.adapter, output_dir=self.directory)
        self.assertFalse(self.directory.exists())

    def test_changed_input_response_or_baseline_hash_rejected(self):
        for key in ('input','baseline_response','input_sha256','response_sha256'):
            training = json.loads(self.args['training_bytes']); training['records'][0][key] = 'changed'
            with self.subTest(key=key), self.assertRaises(shadow.ShadowError):
                p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(training)), adapter=self.adapter, output_dir=self.directory)
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args, baseline_bytes=b'changed'), adapter=self.adapter, output_dir=self.directory)
        self.assertEqual(self.adapter.starts, 0)

    def test_duplicate_training_inputs_and_undeclared_projection_version_rejected(self):
        training = json.loads(self.args['training_bytes'])
        training['records'][1] = copy.deepcopy(training['records'][0])
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(training)), adapter=self.adapter, output_dir=self.directory)
        training = json.loads(self.args['training_bytes']); training['schema_version'] = 'arbitrary/9.0'
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(training)), adapter=self.adapter, output_dir=self.directory)

    def test_training_record_count_and_total_message_bounds_before_adapter(self):
        training = json.loads(self.args['training_bytes'])
        for rows in ([], training['records']*9):
            changed = dict(training, records=rows)
            with self.assertRaises(shadow.ShadowError):
                p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(changed)), adapter=self.adapter, output_dir=self.directory)
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args, training_bytes=b'x'*(p.MAX_TRAINING_BYTES+1)), adapter=self.adapter, output_dir=self.directory)
        self.assertFalse(self.directory.exists())

    def test_combined_valid_rows_cannot_exceed_native_input_limit(self):
        training = json.loads(self.args['training_bytes']); rows=[]
        for i in range(10):
            text = str(i)+'a'*4000; response = 'b'*4000
            rows.append({'input':text,'input_sha256':digest(text.encode()),'baseline_response':response,
                         'response_sha256':digest(response.encode()),'feedback_codes':['result.incorrect']})
        training['records'] = rows
        split = json.loads(self.args['split_bytes']); split['training_input_sha256'] = [x['input_sha256'] for x in rows]
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args, training_bytes=encode(training), split_bytes=encode(split)),
                adapter=self.adapter, output_dir=self.directory)
        self.assertFalse(self.directory.exists())

    def test_plan_tampering_even_rehashed_cannot_change_budget_or_authority(self):
        for key, value in [('max_generation_requests',2),('retries',1),('activation_authorized',True),
                           ('candidate_kind','code_patch'),('proposer_sha256','a'*64),('concurrency',True)]:
            plan=json.loads(self.args['plan_bytes']); plan[key]=value; raw=encode(plan)
            with self.subTest(key=key), self.assertRaisesRegex(p.ProposalError,'proposal.plan_mismatch'):
                p.propose_prompt_candidate(**dict(self.args,plan_bytes=raw,expected_plan_sha256=digest(raw)),
                    adapter=self.adapter,output_dir=self.directory)
        self.assertEqual(self.adapter.starts,0)

    def test_wrong_plan_pin_and_wire_policy_fields_rejected(self):
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args,expected_plan_sha256='a'*64),adapter=self.adapter,output_dir=self.directory)
        policy=json.loads(self.args['wire_policy_bytes']); policy['result_fields']={'answer':'integer'}
        with self.assertRaises(shadow.ShadowError):
            p.propose_prompt_candidate(**dict(self.args,wire_policy_bytes=encode(policy)),adapter=self.adapter,output_dir=self.directory)

    def test_adapter_runtime_or_source_drift_rejected_before_directory(self):
        self.adapter.runtime_policy_sha256='a'*64
        with self.assertRaisesRegex(p.ProposalError,'proposal.adapter_mismatch'):
            self.run_proposal()
        self.assertFalse(self.directory.exists())
        self.adapter.runtime_policy_sha256=ProposalFixtureAdapter.runtime_policy_sha256
        with patch.object(c,'adapter_source_sha256',return_value='a'*64):
            with self.assertRaisesRegex(p.ProposalError,'proposal.adapter_mismatch'):
                self.run_proposal()

    def test_known_terminal_budget_failure_stays_rejected_and_unloads(self):
        self.adapter.mode='budget'; report=self.run_proposal()
        self.assertEqual((report['status'],report['error_code']),('rejected','budget_exhausted'))
        self.assertEqual(report,p.audit_prompt_proposal(**self.audit_args()))
        self.assertEqual((len(self.adapter.requests),self.adapter.finishes),(1,1))
        self.assertFalse((self.directory/'candidate.json').exists())

    def test_malformed_fenced_extra_authority_fields_and_wrong_types_are_not_repaired(self):
        base={'result':{'artifact':'p','rationale':'r'},'actions':[]}
        contents=['not JSON','```json\n'+json.dumps(base)+'\n```',
            json.dumps(dict(base,activation_authorized=True)),
            json.dumps({'result':{'artifact':7,'rationale':'r'},'actions':[]}),
            json.dumps({'result':{'artifact':'p','rationale':'r','approved':True},'actions':[]})]
        for i,content in enumerate(contents):
            self.directory=self.root/str(i); self.adapter=ProposalFixtureAdapter(self.directory)
            self.adapter.content=content; self.args=fixture(self.adapter)
            report=self.run_proposal()
            self.assertEqual((report['status'],report['error_code']),('rejected','invalid_response'))
            self.assertEqual(len(self.adapter.requests),1); self.assertEqual(self.adapter.finishes,1)
            self.assertFalse((self.directory/'candidate.json').exists())
            payload=json.loads((self.directory/'0003.json').read_bytes())['payload']
            self.assertEqual(json.loads(base64.b64decode(payload['receipt_base64']))['content'],content)

    def test_actions_and_blank_or_oversized_candidate_text_rejected_after_cleanup(self):
        cases=[({'artifact':'p','rationale':'r'},['touch /never-execute'],'proposal.actions_proposed'),
               ({'artifact':' ','rationale':'r'},[],'proposal.candidate_text_invalid'),
               ({'artifact':'p','rationale':' '},[],'proposal.candidate_text_invalid'),
               ({'artifact':'x'*(p.MAX_ARTIFACT_BYTES+1),'rationale':'r'},[],'proposal.candidate_text_invalid'),
               ({'artifact':'p','rationale':'x'*(p.MAX_RATIONALE_BYTES+1)},[],'proposal.candidate_text_invalid')]
        for i,(value,actions,error) in enumerate(cases):
            self.directory=self.root/str(i); self.adapter=ProposalFixtureAdapter(self.directory)
            self.args=fixture(self.adapter); self.adapter.content=json.dumps({'result':value,'actions':actions})
            report=self.run_proposal(); self.assertEqual(report['error_code'],error)
            self.assertEqual(self.adapter.finishes,1); self.assertFalse((self.directory/'candidate.json').exists())

    def test_uncertain_invalid_or_oversized_receipts_stop_without_unload_or_candidate(self):
        for mode in ('raise','interrupt','forged-error','uncertain','wrong-request','invalid-utf8','oversized-receipt','oversized-content'):
            self.directory=self.root/mode; self.adapter=ProposalFixtureAdapter(self.directory)
            self.args=fixture(self.adapter); self.adapter.mode=mode
            report=self.run_proposal()
            self.assertEqual(report['status'],'aborted'); self.assertTrue(report['server_state_uncertain'])
            self.assertEqual((len(self.adapter.requests),self.adapter.finishes),(1,0))
            self.assertFalse((self.directory/'candidate.json').exists()); self.assertNotIn('SECRET',json.dumps(report))

    def test_start_or_finish_failure_never_publishes_candidate(self):
        for mode in ('bad-start','bad-finish'):
            self.directory=self.root/mode; self.adapter=ProposalFixtureAdapter(self.directory)
            self.args=fixture(self.adapter); self.adapter.mode=mode
            result=self.run_proposal(); self.assertEqual(result['status'],'aborted')
            self.assertEqual(len(self.adapter.requests),0 if mode=='bad-start' else 1)
            self.assertFalse((self.directory/'candidate.json').exists())

    def test_candidate_binding_detects_alteration_or_missing_artifact(self):
        self.run_proposal(); args=self.audit_args()
        for candidate in (None,b'{}',args['candidate_bytes']+b'\n'):
            with self.assertRaisesRegex(p.ProposalError,'proposal.candidate_binding_mismatch'):
                p.audit_prompt_proposal(**dict(args,candidate_bytes=candidate))

    def test_rejected_recording_cannot_be_given_a_candidate(self):
        self.adapter.mode='budget'; self.run_proposal()
        with self.assertRaisesRegex(p.ProposalError,'proposal.unexpected_candidate'):
            p.audit_prompt_proposal(**dict(self.audit_args(),candidate_bytes=b'{}'))

    def test_recording_tampering_wrong_pin_truncation_and_extension_rejected(self):
        self.run_proposal(); args=self.audit_args()
        with self.assertRaises(shadow.ShadowError):
            p.audit_prompt_proposal(**dict(args,expected_recording_sha256='a'*64))
        raw=json.loads(args['recording_bytes'])
        for events in (raw['events'][:-1],raw['events']+[raw['events'][-1]]):
            changed=encode({'events':events})
            with self.assertRaises(p.ProposalError):
                p.audit_prompt_proposal(**dict(args,recording_bytes=changed,expected_recording_sha256=digest(changed)))

    def test_rechained_wrong_request_manifest_or_lifecycle_cannot_pass_audit(self):
        self.run_proposal(); args=self.audit_args()
        for kind in ('request','manifest','lifecycle','sequence'):
            raw=json.loads(args['recording_bytes'])
            if kind=='request': raw['events'][2]['payload']['request']='{}'
            if kind=='manifest': raw['events'][0]['payload']['proposer_sha256']='a'*64
            if kind=='lifecycle': raw['events'][4]['payload']=c._capture(encode(dict(c._LIFECYCLE,idle=False)))
            if kind=='sequence': raw['events'][2]['kind']='unexpected'
            changed=rechain(raw)
            with self.subTest(kind=kind),self.assertRaises(shadow.ShadowError):
                p.audit_prompt_proposal(**dict(args,recording_bytes=changed,expected_recording_sha256=digest(changed)))

    def test_auditing_has_no_adapter_or_filesystem_effects(self):
        self.run_proposal(); before={str(x.relative_to(self.directory)):x.read_bytes() for x in self.directory.rglob('*') if x.is_file()}
        calls=len(self.adapter.requests); p.audit_prompt_proposal(**self.audit_args())
        after={str(x.relative_to(self.directory)):x.read_bytes() for x in self.directory.rglob('*') if x.is_file()}
        self.assertEqual(before,after); self.assertEqual(len(self.adapter.requests),calls)

    def test_partial_storage_failure_cannot_claim_a_success_or_generate_again(self):
        original=shadow._publish
        def fail_candidate(path,data):
            if path.name=='candidate.json': raise OSError('SECRET storage path')
            return original(path,data)
        with patch.object(shadow,'_publish',side_effect=fail_candidate):
            report=self.run_proposal()
        self.assertEqual(report['status'],'aborted'); self.assertTrue(report['cleanup_confirmed'])
        self.assertEqual((len(self.adapter.requests),self.adapter.finishes),(1,1))
        self.assertTrue((self.directory/'recording.json').exists()); self.assertNotIn('SECRET',json.dumps(report))

    def test_private_modes_report_redaction_and_no_authority(self):
        report=self.run_proposal()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode),0o700)
        for path in self.directory.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700 if path.is_dir() else 0o600)
        self.assertNotIn('PRIVATE',json.dumps(report)); self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['execution_attested']); self.assertEqual(report['candidate_state'],'quarantined')

    def test_existing_output_and_nonposix_never_start_adapter(self):
        self.directory.mkdir()
        with self.assertRaises(FileExistsError): self.run_proposal()
        self.assertEqual(self.adapter.starts,0)
        with patch.object(c.os,'name','nt'):
            with self.assertRaises(p.ProposalError): self.run_proposal()


if __name__=='__main__':
    unittest.main()
