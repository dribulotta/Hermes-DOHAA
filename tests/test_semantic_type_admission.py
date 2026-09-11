"""Malformed JSON selectors must be rejected through each public entry point."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.assurance.gates import SemanticAssertionsGate
from hermes_dohaa.assurance.semantic_assertions import parse_semantic_assertions
from hermes_dohaa.cli import main
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController, RunStatus
from hermes_dohaa.controller.semantic_repair import propose_deterministic_semantic_repair
from hermes_dohaa.evaluation.models import EvaluationCase, EvaluationSuiteError
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Proposal
from test_evaluation import suite_dict
from test_semantic_assertions import assertion, expression, ref


SELECTORS = ('operator', 'op', 'source', 'comparator', 'order')
MARKER = 'synthetic-selector-payload'
UNHASHABLE = ([MARKER], {'unexpected': MARKER})


def case_with_rule(selector, value, *, replace=True):
    case = suite_dict()['cases'][0]
    inputs = case['contract']['inputs']
    inputs.update(items=[{'value': 2}, {'value': 1}], threshold=1)
    filtered = expression('filter', ref('inputs', '/items'),
                          ref('inputs', '/threshold'), pointer='/value',
                          comparator='greater_than_or_equal')
    ordered = expression('sort_by', filtered, pointer='/value', order='ascending')
    rule = assertion('ordered-values', 'equals', ref('result', '/answer'), ordered)
    if replace:
        targets = {'operator': rule, 'op': ordered, 'source': filtered['args'][0],
                   'comparator': filtered, 'order': ordered}
        targets[selector][selector] = copy.deepcopy(value)
    inputs['semantic_assertions'] = [rule]
    inputs['result_spec']['types']['answer'] = 'array'
    case['expected_result'] = {'answer': [{'value': 1}, {'value': 2}]}
    return case


def malformed_cases():
    for selector in SELECTORS:
        for value in UNHASHABLE:
            yield selector, value, case_with_rule(selector, value)


class SemanticTypeAdmissionTests(unittest.TestCase):
    def test_parser_rejects_every_non_string_json_selector_without_echoing_values(self):
        for selector in SELECTORS:
            for value in (*UNHASHABLE, None, True, 1, 1.5):
                case = case_with_rule(selector, value)
                with self.subTest(selector=selector, value=value):
                    with self.assertRaises(ValueError) as caught:
                        parse_semantic_assertions(case['contract']['inputs']['semantic_assertions'])
                    self.assertNotIn(MARKER, str(caught.exception))

    def test_gate_records_invalid_spec_instead_of_raising(self):
        for selector, value, case in malformed_cases():
            with self.subTest(selector=selector, value=value):
                result = SemanticAssertionsGate().evaluate(
                    TaskContract.from_dict(case['contract']), Proposal(result=case['expected_result']))
                self.assertFalse(result.passed)
                self.assertEqual(result.failure_code, 'semantic.spec_invalid')
                self.assertNotIn(MARKER, json.dumps(result.to_dict()))

    def test_cli_validate_and_run_reject_before_runtime_or_ledger_creation(self):
        for selector, value, case in malformed_cases():
            for command in ('validate', 'run'):
                with self.subTest(selector=selector, value=value, command=command):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        contract, ledger = root/'contract.json', root/'ledger.sqlite3'
                        contract.write_text(json.dumps(case['contract']), encoding='utf-8')
                        args = [command, str(contract)]
                        if command == 'run':
                            args += ['--ledger', str(ledger)]
                        output = io.StringIO()
                        with patch('hermes_dohaa.cli._runtime_from_args') as runtime, redirect_stdout(output):
                            code = main(args)
                        self.assertEqual(code, 2)
                        self.assertFalse(json.loads(output.getvalue())['valid'])
                        self.assertIn('semantic_assertions', output.getvalue())
                        runtime.assert_not_called()
                        self.assertFalse(ledger.exists())

    def test_evaluation_admission_raises_its_documented_error(self):
        for selector, value, case in malformed_cases():
            with self.subTest(selector=selector, value=value):
                with self.assertRaisesRegex(EvaluationSuiteError, 'semantic_assertions'):
                    EvaluationCase.from_dict(case)

    def test_freeze_rejects_before_writing_a_suite_commitment(self):
        for selector, value, case in malformed_cases():
            with self.subTest(selector=selector, value=value):
                suite = suite_dict()
                suite['cases'][0] = case
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source, output = root/'suite.json', root/'commitment.json'
                    source.write_text(json.dumps(suite), encoding='utf-8')
                    with redirect_stdout(io.StringIO()):
                        code = main(['freeze-suite', str(source), '--output', str(output),
                                     '--protocol-commit', 'a'*40])
                    self.assertEqual(code, 2)
                    self.assertFalse(output.exists())

    def test_repair_rejects_without_mutating_contract_or_proposal(self):
        for selector, value, case in malformed_cases():
            with self.subTest(selector=selector, value=value):
                contract = TaskContract.from_dict(case['contract'])
                proposal = Proposal(result={'answer': []})
                before = copy.deepcopy((contract.to_dict(), proposal.to_dict()))
                self.assertIsNone(propose_deterministic_semantic_repair(contract, proposal))
                self.assertEqual((contract.to_dict(), proposal.to_dict()), before)

    def test_direct_controller_records_one_terminal_within_existing_budget(self):
        for selector, value, case in malformed_cases():
            case['contract']['max_attempts'] = 1
            with self.subTest(selector=selector, value=value), EvidenceLedger() as ledger:
                with patch('hermes_dohaa.cli.HermesApiRuntime') as runtime_factory:
                    runtime = runtime_factory.return_value
                    runtime.propose.return_value = Proposal(result={'answer': []})
                    result = DohaaController(runtime, (SemanticAssertionsGate(),), ledger).run(
                        TaskContract.from_dict(case['contract']))
                self.assertEqual(result.status, RunStatus.ESCALATED)
                runtime.propose.assert_called_once()
                self.assertEqual(result.gate_results[0].failure_code, 'semantic.spec_invalid')
                self.assertEqual(sum(r.event_type == 'run.finished' for r in ledger.records()), 1)
                self.assertTrue(ledger.verify_chain())

    def test_valid_nested_selectors_and_default_sort_order_keep_their_meaning(self):
        for order in ('ascending', 'descending', None):
            case = case_with_rule('order', order, replace=False)
            ordered = case['contract']['inputs']['semantic_assertions'][0]['right']
            if order is None:
                del ordered['order']
            else:
                ordered['order'] = order
            expected = case['expected_result']
            if order == 'descending':
                expected['answer'].reverse()
            with self.subTest(order=order):
                loaded = EvaluationCase.from_dict(case)
                self.assertEqual(loaded.expected_result, expected)
                self.assertTrue(SemanticAssertionsGate().evaluate(
                    loaded.contract, Proposal(result=expected)).passed)
                repaired = propose_deterministic_semantic_repair(
                    loaded.contract, Proposal(result={'answer': []}))
                self.assertEqual(repaired.proposal.result, expected)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/'contract.json'
                    path.write_text(json.dumps(case['contract']), encoding='utf-8')
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['validate', str(path)]), 0)


if __name__ == '__main__':
    unittest.main()
