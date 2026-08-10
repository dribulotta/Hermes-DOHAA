import json
import unittest
from pathlib import Path

from hermes_dohaa.assurance.gates import GateResult, ResultEqualsGate, ResultSpecGate
from hermes_dohaa.assurance.result_spec import parse_result_spec
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evaluation.models import EvaluationSuite, EvaluationSuiteError
from hermes_dohaa.evaluation.runner import _repair_transition, structural_distance
from hermes_dohaa.evaluation.statistics import analyze_unique_cases
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback


def contract(spec):
    return TaskContract.from_dict({
        "schema_version":"1.0", "contract_id":"synthetic", "objective":"Validate synthetic output",
        "inputs":{"result_spec":spec}, "allowed_actions":[], "forbidden_actions":[],
        "acceptance_criteria":[{"criterion_id":"synthetic-check","description":"Synthetic result is valid","required_evidence":[]}], "risk_level":"low", "requires_human_approval":False,
        "max_attempts":2,
    })


class RecursiveResultTests(unittest.TestCase):
    def test_flat_compatibility(self):
        spec={"required_keys":["answer"],"additional_keys":False,"types":{"answer":"string"},"enums":{}}
        self.assertTrue(ResultSpecGate().evaluate(contract(spec), Proposal({"answer":"ok"})).passed)

    def test_nested_diagnostics_pointer_and_multiple(self):
        spec={"spec_version":"2.0","type":"object","required":["a/b","items"],"additional_properties":False,
              "properties":{"a/b":{"type":"integer"},"items":{"type":"array","items":{"type":"object",
              "required":["x~y"],"additional_properties":False,"properties":{"x~y":{"type":"string"}}}}}}
        result=ResultSpecGate().evaluate(contract(spec), Proposal({"a/b":"bad","items":[{"x~y":1,"extra":0}]}))
        self.assertFalse(result.passed)
        self.assertEqual(3, result.details["violation_count"])
        self.assertEqual(["/a~1b", "/items/0", "/items/0/x~0y"], [v["path"] for v in result.details["violations"]])

    def test_limit_100(self):
        spec={"spec_version":"2.0","type":"array","items":{"type":"integer"}}
        result=ResultSpecGate().evaluate(contract(spec), Proposal(["x"]*105))
        self.assertEqual(105,result.details["violation_count"])
        self.assertEqual(100,result.details["reported_violation_count"])
        self.assertTrue(result.details["truncated"])

    def test_max_depth_and_invalid_pre_runtime(self):
        node={"type":"string"}
        for _ in range(33): node={"type":"array","items":node}
        with self.assertRaises(ValueError): parse_result_spec({"spec_version":"2.0",**node})
        raw=json.loads(Path("examples/evaluation-suite.json").read_text())
        raw["cases"][0]["contract"]["inputs"]["result_spec"]={"spec_version":"2.0","type":"string","items":{"type":"string"}}
        with self.assertRaises(EvaluationSuiteError): EvaluationSuite.from_dict(raw)

    def test_strict_json_equality(self):
        gate=ResultEqualsGate(True)
        self.assertFalse(gate.evaluate(contract({"required_keys":["x"],"additional_keys":True,"types":{},"enums":{}}),Proposal(1)).passed)
        self.assertFalse(ResultEqualsGate(1).evaluate(contract({"required_keys":["x"],"additional_keys":True,"types":{},"enums":{}}),Proposal(1.0)).passed)
        self.assertTrue(ResultEqualsGate({"a":1,"b":2}).evaluate(contract({"required_keys":["x"],"additional_keys":True,"types":{},"enums":{}}),Proposal({"b":2,"a":1})).passed)
        self.assertEqual("result.mismatch", gate.evaluate(contract({"required_keys":["x"],"additional_keys":True,"types":{},"enums":{}}),Proposal(1)).failure_code)

    def test_details_round_trips_and_detaches(self):
        source={"items":[{"x":1}]}
        result=GateResult("g",False,"bad",failure_code="bad",details=source)
        source["items"][0]["x"]=9
        self.assertEqual(1,GateResult.from_dict(result.to_dict()).details["items"][0]["x"])
        feedback=result.to_feedback()
        self.assertEqual(feedback,VerifierFeedback.from_dict(feedback.to_dict()))
        self.assertIn("details", feedback.to_dict())
        self.assertNotIn("expected_result", json.dumps(feedback.to_dict()))

    def test_distance_and_all_transitions(self):
        self.assertEqual({"mismatch_count":2,"kind_counts":{"unexpected_key":1,"value_mismatch":1}},
                         structural_distance({"a":2,"b":3},{"a":1}))
        def score(passed,distance): return {"all_gates_passed":passed,"oracle_distance":{"mismatch_count":distance}}
        cases=[(True,0,True,0,"passed_unchanged"),(False,1,True,0,"repaired"),
               (False,2,False,1,"partial_improvement"),(False,1,False,1,"unchanged_failure"),
               (False,1,False,2,"worsened_failure"),(True,0,False,1,"regressed")]
        for a,b,c,d,label in cases: self.assertEqual(label,_repair_transition(score(a,b),score(c,d)))

    def test_schema_json(self):
        schema=json.loads(Path("schemas/evaluation-suite.schema.json").read_text())
        self.assertIn("recursiveResultSpec",schema["$defs"])

    def test_domain_statistics_keep_runtime_failures(self):
        def outcome(passed, runtime=False):
            return {"status":"runtime_failed" if runtime else "completed",
                    "final_score":None if runtime else {"all_gates_passed":passed}}
        trials=[{"case_id":"synthetic-a","domain":"alpha","conditions":{
            "direct":outcome(True),"self_reflection":outcome(False),"dohaa":outcome(False,True)}},
            {"case_id":"synthetic-b","domain":"beta","conditions":{
            "direct":outcome(False),"self_reflection":outcome(True),"dohaa":outcome(True)}}]
        stats=analyze_unique_cases(trials)["domain_statistics"]
        self.assertTrue(stats["alpha"]["exploratory"])
        self.assertEqual(0,stats["alpha"]["condition_statistics"]["dohaa"]["strict_passes"])
        self.assertEqual(1,stats["beta"]["unique_cases"])


if __name__ == "__main__": unittest.main()
