"""Admission regressions for the three review findings on PR #46."""
import copy, io, json, tempfile, unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from hermes_dohaa.assurance.evidence_policy import parse_evidence_policy
from hermes_dohaa.assurance.gates import ClaimEvidenceGate
from hermes_dohaa.cli import _validate_contract_gate_inputs, main
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evaluation.models import EvaluationCase, EvaluationSuiteError
from hermes_dohaa.runtime.base import Claim, Proposal
from test_evidence_policy import fixture, task
from test_evaluation import suite_dict

def evaluation_case(policy):
    raw=task(policy).to_dict()
    raw['max_attempts']=2
    raw['inputs']['result_spec']={'required_keys':['stock'],'additional_keys':False,'types':{'stock':'integer'},'enums':{}}
    return {'case_id':'evidence-admission','domain':'evidence_synthesis','contract':raw,'expected_result':{'stock':29}}

class EvidencePolicyAdmissionTests(unittest.TestCase):
    def test_evaluation_rejects_malformed_declared_policy(self):
        valid,_=fixture()
        for value in (None,{},[],{**valid,'claims':[]}):
            with self.subTest(value=value),self.assertRaisesRegex(EvaluationSuiteError,'evidence_policy'):
                EvaluationCase.from_dict(evaluation_case(value))

    def test_freeze_suite_rejects_invalid_policy_before_writing_commitment(self):
        raw=suite_dict();raw['cases'][0]['contract']['inputs']['evidence_policy']=None
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'suite.json';output=root/'commitment.json'
            source.write_text(json.dumps(raw),encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                code=main(['freeze-suite',str(source),'--output',str(output),'--protocol-commit','a'*40])
            self.assertEqual(code,2);self.assertFalse(output.exists())

    def test_all_required_ids_must_be_committed_in_cli_and_evaluation(self):
        policy,proposal=fixture();case=evaluation_case(policy)
        case['contract']['acceptance_criteria'].append({'criterion_id':'second','description':'Additional source required','required_evidence':['not-committed']})
        contract=TaskContract.from_dict(case['contract'])
        with self.assertRaisesRegex(ValueError,'evidence_policy'):_validate_contract_gate_inputs(contract)
        with self.assertRaisesRegex(EvaluationSuiteError,'evidence_policy'):EvaluationCase.from_dict(case)
        self.assertEqual(ClaimEvidenceGate().evaluate(contract,proposal).failure_code,'evidence.policy_invalid')

    def test_outer_whitespace_rejected_without_trimming_policy(self):
        for prefix,suffix in ((' Stock is ','.'),('\tStock is ','.'),('\u00a0Stock is ','.'),('Stock is ','. '),('Stock is ','.\n'),('',' ')):
            policy,_=fixture();policy['claims'][0].update(prefix=prefix,suffix=suffix);before=copy.deepcopy(policy)
            with self.subTest(prefix=prefix,suffix=suffix),self.assertRaisesRegex(ValueError,'whitespace'):parse_evidence_policy(policy)
            self.assertEqual(policy,before)

    def test_separator_whitespace_and_empty_affixes_remain_satisfiable(self):
        for prefix,suffix in (('Stock is ','.'),('Stock is ',' units'),('','')):
            policy,proposal=fixture();policy['claims'][0].update(prefix=prefix,suffix=suffix)
            candidate=replace(proposal,claims=(Claim(prefix+'29'+suffix,('source-1',)),))
            candidate=Proposal.from_dict(candidate.to_dict())
            contract=task(policy);_validate_contract_gate_inputs(contract)
            self.assertTrue(ClaimEvidenceGate().evaluate(contract,candidate).passed)

    def test_valid_policy_loads_without_private_expected_value_validation(self):
        policy,_=fixture();case=evaluation_case(policy)
        case['expected_result']={'stock':901}
        # Admission validates the declared policy, not the scoring oracle.
        self.assertEqual(EvaluationCase.from_dict(case).expected_result,{'stock':901})

    def test_absent_policy_keeps_legacy_contract_admission(self):
        policy,_=fixture();case=evaluation_case(policy);del case['contract']['inputs']['evidence_policy']
        case['contract']['acceptance_criteria'][0]['required_evidence']=['legacy-source']
        contract=TaskContract.from_dict(case['contract']);_validate_contract_gate_inputs(contract)
        self.assertEqual(EvaluationCase.from_dict(case).contract,contract)

if __name__=='__main__':unittest.main()
