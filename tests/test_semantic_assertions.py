import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hermes_dohaa.assurance.gates import (
    GateFailureCode,
    SemanticAssertionsGate,
)
from hermes_dohaa.assurance.semantic_assertions import (
    MAX_COLLECTION_ITEMS,
    parse_semantic_assertions,
)
from hermes_dohaa.cli import _contract_gates, main
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.evaluation import (
    EvaluationSuite,
    EvaluationSuiteError,
    run_comparative_evaluation,
)
from hermes_dohaa.runtime.base import Proposal
from test_contracts import valid_contract


def ref(source, pointer):
    return {"op": "ref", "source": source, "pointer": pointer}


def expression(op, *args, **fields):
    return {"op": op, "args": list(args), **fields}


def assertion(assertion_id, operator, left, right):
    return {
        "assertion_id": assertion_id,
        "operator": operator,
        "left": left,
        "right": right,
    }


def semantic_contract(inputs, *, max_attempts=2):
    return TaskContract.from_dict(
        valid_contract(
            inputs=inputs,
            max_attempts=max_attempts,
            acceptance_criteria=[
                {
                    "criterion_id": "semantic-result",
                    "description": "The result satisfies visible assertions.",
                    "required_evidence": [],
                }
            ],
            allowed_actions=[],
        )
    )


class SemanticAssertionTests(unittest.TestCase):
    def test_arithmetic_assertion_passes_and_feedback_is_value_free(self):
        inputs = {
            "budget": {
                "total": 120,
                "spent": 80,
                "committed": 20,
            },
            "semantic_assertions": [
                assertion(
                    "available-budget",
                    "equals",
                    ref("result", "/available_budget"),
                    expression(
                        "subtract",
                        ref("inputs", "/budget/total"),
                        expression(
                            "add",
                            ref("inputs", "/budget/spent"),
                            ref("inputs", "/budget/committed"),
                        ),
                    ),
                )
            ],
        }
        contract = semantic_contract(inputs)
        gate = SemanticAssertionsGate()

        self.assertTrue(
            gate.evaluate(
                contract,
                Proposal(result={"available_budget": 20}),
            ).passed
        )
        failure = gate.evaluate(
            contract,
            Proposal(result={"available_budget": 21}),
        )

        self.assertFalse(failure.passed)
        self.assertEqual(
            failure.failure_code,
            GateFailureCode.SEMANTIC_ASSERTION_FAILED,
        )
        self.assertEqual(
            failure.details["violations"],
            [
                {
                    "assertion_id": "available-budget",
                    "code": "semantic.assertion_failed",
                    "operator": "equals",
                }
            ],
        )
        serialized = json.dumps(failure.to_feedback().to_dict())
        self.assertNotIn("expected_result", serialized)
        self.assertNotIn("oracle", serialized)
        self.assertNotIn("actual_value", serialized)
        self.assertNotIn("expected_value", serialized)

    def test_collection_pipeline_orders_projects_filters_and_compares_sets(self):
        events = [
            {
                "event_id": "e1",
                "timestamp": "2026-08-10T09:20:00Z",
                "state": "degraded",
            },
            {
                "event_id": "e2",
                "timestamp": "2026-08-10T09:00:00Z",
                "state": "open",
            },
            {
                "event_id": "e3",
                "timestamp": "2026-08-10T10:00:00Z",
                "state": "restored",
            },
        ]
        sorted_events = expression(
            "sort_by",
            ref("inputs", "/events"),
            pointer="/timestamp",
        )
        active_items = expression(
            "filter",
            ref("inputs", "/inventory"),
            ref("inputs", "/active_status"),
            pointer="/status",
            comparator="equals",
        )
        inputs = {
            "events": events,
            "inventory": [
                {"item_id": "a", "status": "active"},
                {"item_id": "b", "status": "inactive"},
                {"item_id": "c", "status": "active"},
            ],
            "active_status": "active",
            "semantic_assertions": [
                assertion(
                    "timeline-order",
                    "equals",
                    ref("result", "/ordered_event_ids"),
                    expression("project", sorted_events, pointer="/event_id"),
                ),
                assertion(
                    "latest-state",
                    "equals",
                    ref("result", "/latest_state"),
                    expression(
                        "at",
                        expression("project", sorted_events, pointer="/state"),
                        index=-1,
                    ),
                ),
                assertion(
                    "active-items",
                    "set_equals",
                    ref("result", "/active_item_ids"),
                    expression("project", active_items, pointer="/item_id"),
                ),
            ],
        }
        contract = semantic_contract(inputs)
        result = SemanticAssertionsGate().evaluate(
            contract,
            Proposal(
                result={
                    "ordered_event_ids": ["e2", "e1", "e3"],
                    "latest_state": "restored",
                    "active_item_ids": ["c", "a"],
                }
            ),
        )

        self.assertTrue(result.passed)

    def test_temporal_assertions_are_timezone_aware_and_business_day_bounded(self):
        inputs = {
            "window": {
                "start": "2026-08-10T09:15:00-03:00",
                "end": "2026-08-10T10:45:00-03:00",
            },
            "calendar": {
                "start_date": "2026-08-07",
                "business_days": 1,
                "holidays": [],
            },
            "semantic_assertions": [
                assertion(
                    "duration",
                    "equals",
                    ref("result", "/elapsed_minutes"),
                    expression(
                        "duration_minutes",
                        ref("inputs", "/window/start"),
                        ref("inputs", "/window/end"),
                    ),
                ),
                assertion(
                    "business-deadline",
                    "equals",
                    ref("result", "/due_date"),
                    expression(
                        "add_business_days",
                        ref("inputs", "/calendar/start_date"),
                        ref("inputs", "/calendar/business_days"),
                        ref("inputs", "/calendar/holidays"),
                    ),
                ),
            ],
        }

        result = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            Proposal(
                result={
                    "elapsed_minutes": 90,
                    "due_date": "2026-08-10",
                }
            ),
        )

        self.assertTrue(result.passed)

    def test_numeric_aggregate_unique_and_date_operators(self):
        inputs = {
            "numbers": [3, 1, 2],
            "duplicates": [True, 1, 1.0, True],
            "a": 10,
            "b": 4,
            "negative": -4,
            "fraction": 2.345,
            "start_date": "2026-08-10",
            "calendar_days": 3,
            "upper_bound": 50,
            "semantic_assertions": [
                assertion(
                    "difference",
                    "equals",
                    ref("result", "/difference"),
                    expression(
                        "subtract",
                        ref("inputs", "/a"),
                        ref("inputs", "/b"),
                    ),
                ),
                assertion(
                    "product",
                    "equals",
                    ref("result", "/product"),
                    expression(
                        "multiply",
                        ref("inputs", "/a"),
                        ref("inputs", "/b"),
                    ),
                ),
                assertion(
                    "quotient",
                    "equals",
                    ref("result", "/quotient"),
                    expression(
                        "divide",
                        ref("inputs", "/a"),
                        ref("inputs", "/b"),
                    ),
                ),
                assertion(
                    "absolute",
                    "equals",
                    ref("result", "/absolute"),
                    expression("abs", ref("inputs", "/negative")),
                ),
                assertion(
                    "rounded",
                    "equals",
                    ref("result", "/rounded"),
                    expression(
                        "round",
                        ref("inputs", "/fraction"),
                        digits=2,
                    ),
                ),
                assertion(
                    "sum",
                    "equals",
                    ref("result", "/sum"),
                    expression("sum", ref("inputs", "/numbers")),
                ),
                assertion(
                    "minimum",
                    "equals",
                    ref("result", "/minimum"),
                    expression("min", ref("inputs", "/numbers")),
                ),
                assertion(
                    "maximum",
                    "equals",
                    ref("result", "/maximum"),
                    expression("max", ref("inputs", "/numbers")),
                ),
                assertion(
                    "unique-strict-types",
                    "equals",
                    ref("result", "/unique"),
                    expression("unique", ref("inputs", "/duplicates")),
                ),
                assertion(
                    "calendar-date",
                    "equals",
                    ref("result", "/calendar_date"),
                    expression(
                        "add_days",
                        ref("inputs", "/start_date"),
                        ref("inputs", "/calendar_days"),
                    ),
                ),
                assertion(
                    "upper-bound",
                    "less_than",
                    ref("result", "/product"),
                    ref("inputs", "/upper_bound"),
                ),
                assertion(
                    "different-values",
                    "not_equals",
                    ref("result", "/difference"),
                    ref("result", "/product"),
                ),
            ],
        }
        proposal = Proposal(
            result={
                "difference": 6,
                "product": 40,
                "quotient": 2.5,
                "absolute": 4,
                "rounded": 2.35,
                "sum": 6,
                "minimum": 1,
                "maximum": 3,
                "unique": [True, 1, 1.0],
                "calendar_date": "2026-08-13",
            }
        )
        result = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            proposal,
        )

        self.assertTrue(result.passed)

    def test_ordering_rejects_booleans_and_division_by_zero_is_explicit(self):
        inputs = {
            "flag": True,
            "zero": 0,
            "numerator": 10,
            "semantic_assertions": [
                assertion(
                    "boolean-order",
                    "less_than",
                    ref("result", "/flag"),
                    ref("inputs", "/flag"),
                ),
                assertion(
                    "zero-division",
                    "equals",
                    ref("result", "/ratio"),
                    expression(
                        "divide",
                        ref("inputs", "/numerator"),
                        ref("inputs", "/zero"),
                    ),
                ),
            ],
        }

        failure = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            Proposal(result={"flag": False, "ratio": 0}),
        )

        self.assertEqual(
            [
                item["error_code"]
                for item in failure.details["violations"]
            ],
            ["comparison.type_mismatch", "numeric.division_by_zero"],
        )

    def test_evaluation_errors_are_safe_and_machine_readable(self):
        secret_input = "VISIBLE_INPUT_SENTINEL"
        secret_result = "PROPOSAL_SENTINEL"
        inputs = {
            "reference": secret_input,
            "semantic_assertions": [
                assertion(
                    "missing-field",
                    "equals",
                    ref("result", "/missing"),
                    ref("inputs", "/reference"),
                )
            ],
        }

        failure = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            Proposal(result={"provided": secret_result}),
        )

        self.assertEqual(
            failure.failure_code,
            GateFailureCode.SEMANTIC_EVALUATION_ERROR,
        )
        violation = failure.details["violations"][0]
        self.assertEqual(violation["error_code"], "reference.missing")
        self.assertEqual(violation["source"], "result")
        self.assertEqual(violation["pointer"], "/missing")
        serialized = json.dumps(failure.to_feedback().to_dict())
        self.assertNotIn(secret_input, serialized)
        self.assertNotIn(secret_result, serialized)

    def test_parser_rejects_unsafe_or_unbounded_expressions(self):
        base = assertion(
            "fixture",
            "equals",
            ref("result", "/value"),
            ref("inputs", "/fact"),
        )
        invalid = []

        literal = copy.deepcopy(base)
        literal["right"] = {"op": "literal", "value": "hidden"}
        invalid.append(literal)

        root_input = copy.deepcopy(base)
        root_input["right"] = ref("inputs", "")
        invalid.append(root_input)

        reserved = copy.deepcopy(base)
        reserved["right"] = ref("inputs", "/result_spec")
        invalid.append(reserved)

        repair_control = copy.deepcopy(base)
        repair_control["right"] = ref("inputs", "/repair_policy")
        invalid.append(repair_control)

        malformed_pointer = copy.deepcopy(base)
        malformed_pointer["right"] = ref("inputs", "/bad~2pointer")
        invalid.append(malformed_pointer)

        oversized_pointer = copy.deepcopy(base)
        oversized_pointer["right"] = ref("inputs", "/" + "x" * 2048)
        invalid.append(oversized_pointer)

        padded_description = copy.deepcopy(base)
        padded_description["description"] = " " + "x" * 1024 + " "
        invalid.append(padded_description)

        padded_group = copy.deepcopy(base)
        padded_group["repair_group"] = " " + "x" * 128 + " "
        invalid.append(padded_group)

        null_description = copy.deepcopy(base)
        null_description["description"] = None
        invalid.append(null_description)

        null_group = copy.deepcopy(base)
        null_group["repair_group"] = None
        invalid.append(null_group)

        unknown_field = copy.deepcopy(base)
        unknown_field["right"]["value"] = "not-allowed"
        invalid.append(unknown_field)

        input_tautology = copy.deepcopy(base)
        input_tautology["left"] = ref("inputs", "/fact")
        invalid.append(input_tautology)

        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_semantic_assertions([raw])

        duplicate = [base, copy.deepcopy(base)]
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            parse_semantic_assertions(duplicate)

        deep = ref("result", "/value")
        for _ in range(18):
            deep = expression("abs", deep)
        too_deep = copy.deepcopy(base)
        too_deep["left"] = deep
        with self.assertRaisesRegex(ValueError, "maximum depth"):
            parse_semantic_assertions([too_deep])

        invalid_gate = SemanticAssertionsGate().evaluate(
            semantic_contract({"semantic_assertions": []}),
            Proposal(result={}),
        )
        self.assertEqual(
            invalid_gate.failure_code,
            GateFailureCode.SEMANTIC_SPEC_INVALID,
        )

    def test_collection_bound_fails_closed_without_echoing_collection(self):
        inputs = {
            "items": ["sensitive"] * (MAX_COLLECTION_ITEMS + 1),
            "semantic_assertions": [
                assertion(
                    "bounded-length",
                    "equals",
                    ref("result", "/count"),
                    expression("length", ref("inputs", "/items")),
                )
            ],
        }
        failure = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            Proposal(result={"count": MAX_COLLECTION_ITEMS + 1}),
        )

        violation = failure.details["violations"][0]
        self.assertEqual(violation["error_code"], "collection.too_large")
        self.assertNotIn("sensitive", json.dumps(failure.to_dict()))

    def test_numeric_magnitude_bound_fails_closed(self):
        inputs = {
            "large": 10**101,
            "other": 1,
            "semantic_assertions": [
                assertion(
                    "bounded-number",
                    "equals",
                    ref("result", "/total"),
                    expression(
                        "add",
                        ref("inputs", "/large"),
                        ref("inputs", "/other"),
                    ),
                )
            ],
        }

        failure = SemanticAssertionsGate().evaluate(
            semantic_contract(inputs),
            Proposal(result={"total": 0}),
        )

        violation = failure.details["violations"][0]
        self.assertEqual(violation["error_code"], "numeric.out_of_range")
        self.assertEqual(violation["maximum_absolute_exponent"], 100)

    def test_cli_includes_visible_contract_gates_without_changing_plain_contracts(self):
        plain = semantic_contract({})
        semantic = semantic_contract(
            {
                "result_spec": {
                    "required_keys": ["value"],
                    "additional_keys": False,
                    "types": {"value": "integer"},
                    "enums": {},
                },
                "fact": 1,
                "semantic_assertions": [
                    assertion(
                        "visible-value",
                        "equals",
                        ref("result", "/value"),
                        ref("inputs", "/fact"),
                    )
                ],
            }
        )

        self.assertEqual(
            [gate.name for gate in _contract_gates(plain)],
            ["action_policy", "claim_evidence", "required_evidence"],
        )
        self.assertEqual(
            [gate.name for gate in _contract_gates(semantic)],
            [
                "result_spec",
                "semantic_assertions",
                "action_policy",
                "claim_evidence",
                "required_evidence",
            ],
        )

    def test_cli_validate_rejects_invalid_assertions_before_runtime(self):
        raw = valid_contract(
            inputs={
                "semantic_assertions": [
                    assertion(
                        "unsafe-literal",
                        "equals",
                        ref("result", "/value"),
                        {"op": "literal", "value": "forbidden"},
                    )
                ]
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["validate", str(path)])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["valid"])
        self.assertIn("invalid semantic_assertions", payload["error"])


class VisibleSemanticRuntime:
    def __init__(self):
        self.feedback = []

    def propose(self, contract, feedback):
        self.feedback.append(tuple(item.to_dict() for item in feedback))
        codes = {item.code for item in feedback}
        if "semantic.assertion_failed" in codes:
            return Proposal(result={"answer": contract.inputs["fact"]})
        return Proposal(result={"answer": "wrong"})


class VisibleSemanticFactory:
    def __init__(self):
        self.runtimes = []

    def __call__(self, contract, session_id, sampling_seed):
        del contract, session_id, sampling_seed
        runtime = VisibleSemanticRuntime()
        self.runtimes.append(runtime)
        return runtime


def semantic_suite_dict():
    cases = []
    for index, domain in enumerate(
        ("evidence_synthesis", "structured_extraction"),
        1,
    ):
        fact = f"visible-answer-{index}"
        inputs = {
            "fact": fact,
            "result_spec": {
                "required_keys": ["answer"],
                "additional_keys": False,
                "types": {"answer": "string"},
                "enums": {},
            },
            "semantic_assertions": [
                assertion(
                    "answer-from-visible-fact",
                    "equals",
                    ref("result", "/answer"),
                    ref("inputs", "/fact"),
                )
            ],
        }
        cases.append(
            {
                "case_id": f"semantic-case-{index}",
                "domain": domain,
                "contract": valid_contract(
                    contract_id=f"semantic-contract-{index}",
                    objective="Return the answer supplied in visible facts.",
                    inputs=inputs,
                    max_attempts=2,
                    acceptance_criteria=[
                        {
                            "criterion_id": "visible-answer",
                            "description": "Use the visible answer.",
                            "required_evidence": [],
                        }
                    ],
                    allowed_actions=[],
                ),
                "expected_result": {"answer": fact},
            }
        )
    return {
        "schema_version": "1.0",
        "suite_id": "synthetic-semantic-suite",
        "description": "Synthetic contract-visible semantic repair.",
        "cases": cases,
    }


class SemanticEvaluationIntegrationTests(unittest.TestCase):
    def test_invalid_semantic_spec_is_rejected_before_runtime(self):
        raw = semantic_suite_dict()
        raw["cases"][0]["contract"]["inputs"]["semantic_assertions"][0][
            "right"
        ] = {"op": "literal", "value": "hidden"}

        with self.assertRaisesRegex(
            EvaluationSuiteError,
            "invalid semantic_assertions",
        ):
            EvaluationSuite.from_dict(raw)

    def test_runner_reports_semantic_dimension_and_deterministic_repair(self):
        suite = EvaluationSuite.from_dict(semantic_suite_dict())
        factory = VisibleSemanticFactory()

        result = run_comparative_evaluation(suite, factory, seed=16)

        self.assertEqual(result["summary"]["direct"]["final_passes"], 0)
        self.assertEqual(
            result["summary"]["self_reflection"]["final_passes"],
            0,
        )
        self.assertEqual(result["summary"]["dohaa"]["final_passes"], 2)
        self.assertEqual(result["summary"]["dohaa"]["improved"], 2)
        self.assertEqual(
            result["summary"]["dohaa"]["final_dimension_passes"][
                "semantic_assertions"
            ],
            2,
        )
        dohaa_outcomes = [
            case["conditions"]["dohaa"]
            for case in result["cases"]
        ]
        for outcome in dohaa_outcomes:
            initial = {
                item["failure_code"]
                for item in outcome["initial_score"]["gate_results"]
                if not item["passed"]
            }
            self.assertIn("semantic.assertion_failed", initial)
            self.assertTrue(outcome["final_score"]["all_gates_passed"])
            self.assertEqual(outcome["runtime_calls"], 1)

        semantic_feedback = [
            item
            for runtime in factory.runtimes
            for call in runtime.feedback
            for item in call
            if item["code"] == "semantic.assertion_failed"
        ]
        self.assertEqual(len(semantic_feedback), 0)
        serialized = json.dumps(semantic_feedback)
        self.assertNotIn("expected_result", serialized)
        self.assertNotIn("visible-answer-", serialized)


if __name__ == "__main__":
    unittest.main()
