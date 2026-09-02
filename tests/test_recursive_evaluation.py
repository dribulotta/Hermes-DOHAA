import copy
import json
import re
import unittest
from pathlib import Path

from hermes_dohaa.assurance.gates import GateResult, ResultEqualsGate, ResultSpecGate
from hermes_dohaa.assurance.result_spec import parse_result_spec
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evaluation.models import EvaluationSuite, EvaluationSuiteError
from hermes_dohaa.evaluation.runner import _repair_transition, structural_distance
from hermes_dohaa.evaluation.statistics import analyze_unique_cases
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback


REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_SCHEMA_PATH = REPO_ROOT / "schemas/evaluation-suite.schema.json"


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

    def test_flat_required_key_without_constraint_only_requires_presence(self):
        spec={"required_keys":["value"],"additional_keys":False,"types":{},"enums":{}}
        gate=ResultSpecGate()
        for value in (None, True, 7, 2.5, ["item"], {"nested":"value"}):
            with self.subTest(value=value):
                self.assertTrue(gate.evaluate(contract(spec), Proposal({"value":value})).passed)

    def test_flat_untyped_enum_accepts_mixed_json_types(self):
        spec={"required_keys":["value"],"additional_keys":False,"types":{},
              "enums":{"value":[None, True, 1, 1.0, "one"]}}
        gate=ResultSpecGate()
        for value in (None, True, 1, 1.0, "one"):
            with self.subTest(value=value):
                self.assertTrue(gate.evaluate(contract(spec), Proposal({"value":value})).passed)
        self.assertFalse(gate.evaluate(contract(spec), Proposal({"value":False})).passed)

    def test_flat_array_accepts_arbitrary_json_elements(self):
        spec={"required_keys":["items"],"additional_keys":False,
              "types":{"items":"array"},"enums":{}}
        items=[None, True, 1, 1.5, "text", ["nested"], {"key":"value"}]
        result=ResultSpecGate().evaluate(contract(spec), Proposal({"items":items}))
        self.assertTrue(result.passed)

    def test_public_suite_expected_results_match_result_specs(self):
        suite=EvaluationSuite.from_json_file(
            REPO_ROOT / "examples/evaluation-suite.json"
        )
        results={
            case.case_id: ResultSpecGate().evaluate(
                case.contract,
                Proposal(case.expected_result),
            )
            for case in suite.cases
        }
        self.assertEqual(["e2", "e1", "e3"], next(
            case.expected_result["ordered_event_ids"]
            for case in suite.cases
            if case.case_id == "incident-timeline"
        ))
        self.assertTrue(all(result.passed for result in results.values()), results)

    def test_number_accepts_integers_and_floats_but_not_booleans(self):
        specs=(
            {"required_keys":["value"],"additional_keys":False,
             "types":{"value":"number"},"enums":{}},
            {"spec_version":"2.0","type":"object","required":["value"],
             "properties":{"value":{"type":"number"}}},
        )
        for spec in specs:
            gate=ResultSpecGate()
            self.assertTrue(gate.evaluate(contract(spec), Proposal({"value":1})).passed)
            self.assertTrue(gate.evaluate(contract(spec), Proposal({"value":1.5})).passed)
            self.assertFalse(gate.evaluate(contract(spec), Proposal({"value":True})).passed)

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

    def test_recursive_array_rejects_wrong_item_type(self):
        spec={"spec_version":"2.0","type":"array","items":{"type":"integer"}}
        result=ResultSpecGate().evaluate(contract(spec), Proposal([1, "wrong"]))
        self.assertFalse(result.passed)
        self.assertEqual("result.type_mismatch", result.failure_code)
        self.assertEqual("/1", result.details["violations"][0]["path"])

    def test_max_depth_and_invalid_pre_runtime(self):
        node={"type":"string"}
        for _ in range(33): node={"type":"array","items":node}
        with self.assertRaises(ValueError): parse_result_spec({"spec_version":"2.0",**node})
        raw=json.loads(
            (REPO_ROOT / "examples/evaluation-suite.json").read_text()
        )
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
        schema=json.loads(
            SUITE_SCHEMA_PATH.read_text()
        )
        self.assertIn("recursiveResultSpec",schema["$defs"])
        assertion_schema = schema["$defs"]["semanticAssertion"]
        self.assertNotIn("description", assertion_schema["required"])
        self.assertNotIn("repair_group", assertion_schema["required"])
        self.assertEqual(
            {
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "pattern": r"\S",
            },
            assertion_schema["properties"]["description"],
        )
        self.assertEqual(
            {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r"\S",
            },
            assertion_schema["properties"]["repair_group"],
        )

        repair_schema = schema["$defs"]["repairPolicy"]
        self.assertFalse(repair_schema["additionalProperties"])
        self.assertEqual(
            {"schema_version", "mode"},
            set(repair_schema["required"]),
        )
        self.assertEqual(
            "1.0",
            repair_schema["properties"]["schema_version"]["const"],
        )
        self.assertEqual(
            "rule_aware",
            repair_schema["properties"]["mode"]["const"],
        )
        self.assertIs(
            repair_schema["properties"]["preserve_unlisted"]["const"],
            True,
        )
        self.assertIs(
            repair_schema["properties"]["require_strict_improvement"]["const"],
            True,
        )
        contract_overlay = schema["properties"]["cases"]["items"][
            "properties"
        ]["contract"]["allOf"][1]
        self.assertEqual(
            "#/$defs/repairPolicy",
            contract_overlay["properties"]["inputs"]["properties"][
                "repair_policy"
            ]["$ref"],
        )

    def test_rule_aware_schema_declares_valid_and_invalid_boundaries(self):
        schema = json.loads(SUITE_SCHEMA_PATH.read_text())
        assertion_properties = schema["$defs"]["semanticAssertion"]["properties"]
        for field, valid, invalid in (
            ("description", "Copy the visible amount.", ("", " \n\t", "x" * 1025)),
            ("repair_group", "budget.totals", ("", " \n\t", "g" * 129)),
        ):
            field_schema = assertion_properties[field]
            self.assertLessEqual(len(valid), field_schema["maxLength"])
            self.assertIsNotNone(re.search(field_schema["pattern"], valid))
            for value in invalid:
                with self.subTest(field=field, value=value[:20]):
                    self.assertTrue(
                        len(value) < field_schema["minLength"]
                        or len(value) > field_schema["maxLength"]
                        or re.search(field_schema["pattern"], value) is None
                    )

        pointer_schema = schema["$defs"]["proposalPointer"]
        valid_pointers = (
            "/result",
            "/result/requires_human_approval",
            "/claims/0",
            "/evidence/0/source~1name~0version",
            "/requested_actions/0",
        )
        invalid_pointers = (
            "",
            "result/answer",
            "/contract/objective",
            "/result/bad~2escape",
        )
        for pointer in valid_pointers:
            self.assertIsNotNone(re.fullmatch(pointer_schema["pattern"], pointer))
        for pointer in invalid_pointers:
            self.assertIsNone(re.fullmatch(pointer_schema["pattern"], pointer))

        immutable_schema = schema["$defs"]["repairPolicy"]["properties"][
            "immutable_paths"
        ]
        self.assertEqual(256, immutable_schema["maxItems"])
        self.assertTrue(immutable_schema["uniqueItems"])
        self.assertEqual(
            "#/$defs/proposalPointer",
            immutable_schema["items"]["$ref"],
        )

    def test_public_rule_aware_schema_examples_match_suite_parser(self):
        raw = json.loads(
            (REPO_ROOT / "examples/evaluation-suite.json").read_text()
        )
        inputs = raw["cases"][0]["contract"]["inputs"]
        inputs["repair_policy"] = {
            "schema_version": "1.0",
            "mode": "rule_aware",
            "preserve_unlisted": True,
            "require_strict_improvement": True,
            "immutable_paths": ["/result/status"],
        }
        inputs["semantic_assertions"] = [
            {
                "assertion_id": "budget.available",
                "description": "Compute the available budget from visible inputs.",
                "repair_group": "budget.totals",
                "operator": "equals",
                "left": {
                    "op": "ref",
                    "source": "result",
                    "pointer": "/available_budget",
                },
                "right": {
                    "op": "ref",
                    "source": "inputs",
                    "pointer": "/sources/0/content/total_budget",
                },
            }
        ]
        self.assertEqual(
            "budget.available",
            EvaluationSuite.from_dict(raw).cases[0].contract.inputs[
                "semantic_assertions"
            ][0]["assertion_id"],
        )

        invalid = []
        unknown_policy = copy.deepcopy(raw)
        unknown_policy["cases"][0]["contract"]["inputs"]["repair_policy"][
            "unknown"
        ] = True
        invalid.append(unknown_policy)

        unsafe_policy = copy.deepcopy(raw)
        unsafe_policy["cases"][0]["contract"]["inputs"]["repair_policy"][
            "preserve_unlisted"
        ] = False
        invalid.append(unsafe_policy)

        invalid_pointer = copy.deepcopy(raw)
        invalid_pointer["cases"][0]["contract"]["inputs"]["repair_policy"][
            "immutable_paths"
        ] = ["/contract/objective"]
        invalid.append(invalid_pointer)

        empty_description = copy.deepcopy(raw)
        empty_description["cases"][0]["contract"]["inputs"][
            "semantic_assertions"
        ][0]["description"] = " \t"
        invalid.append(empty_description)

        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(EvaluationSuiteError):
                    EvaluationSuite.from_dict(candidate)

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
