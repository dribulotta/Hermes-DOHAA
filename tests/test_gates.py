import unittest

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    GateFailureCode,
    GateResult,
    PolicyDecisionGate,
    PolicyReasonCodeGate,
    RequiredEvidenceGate,
    ResultEqualsGate,
    ResultSpecGate,
)
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Claim, EvidenceItem, Proposal
from test_contracts import valid_contract


class GateTests(unittest.TestCase):
    def setUp(self):
        self.contract = TaskContract.from_dict(valid_contract())
        self.evidence = EvidenceItem.create("source-1", "artifact", "fixture.txt", {"ok": True})

    def test_all_builtin_gates_pass_grounded_proposal(self):
        proposal = Proposal(
            result={"ok": True},
            claims=(Claim("The fixture passed", ("source-1",)),),
            evidence=(self.evidence,),
            requested_actions=("artifact.read",),
        )
        gates = (ActionPolicyGate(), ClaimEvidenceGate(), RequiredEvidenceGate())
        self.assertTrue(all(gate.evaluate(self.contract, proposal).passed for gate in gates))

    def test_action_gate_rejects_undeclared_action(self):
        proposal = Proposal(result={}, requested_actions=("shell.execute",))
        result = ActionPolicyGate().evaluate(self.contract, proposal)
        self.assertFalse(result.passed)
        self.assertIn("not allowlisted", result.reason)
        self.assertEqual(
            result.failure_code,
            GateFailureCode.ACTION_NOT_ALLOWLISTED,
        )

    def test_claim_gate_rejects_missing_reference(self):
        proposal = Proposal(result={}, claims=(Claim("Unsupported", ("missing",)),))
        result = ClaimEvidenceGate().evaluate(self.contract, proposal)
        self.assertFalse(result.passed)
        self.assertIn("missing evidence", result.reason)
        self.assertEqual(
            result.failure_code,
            GateFailureCode.EVIDENCE_REFERENCE_MISSING,
        )

    def test_result_gate_requires_exact_value(self):
        expected = {"marker": "DOHAA_SMOKE_OK", "nonce": "abc"}
        gate = ResultEqualsGate(expected)
        self.assertTrue(gate.evaluate(self.contract, Proposal(result=expected)).passed)
        failure = gate.evaluate(
            self.contract,
            Proposal(
                result={
                    "marker": "DOHAA_SMOKE_OK",
                    "nonce": "wrong",
                }
            ),
        )
        self.assertFalse(failure.passed)
        self.assertEqual(
            failure.failure_code,
            GateFailureCode.RESULT_MISMATCH,
        )

    def test_contract_visible_policy_gates_produce_actionable_feedback(self):
        contract = TaskContract.from_dict(
            valid_contract(
                inputs={
                    "policy": {
                        "approval_required_actions": ["external.publish"],
                    },
                    "hypothetical_request": {
                        "action": "external.publish",
                    },
                    "result_spec": {
                        "required_keys": ["decision", "reason_code"],
                        "additional_keys": False,
                        "types": {
                            "decision": "string",
                            "reason_code": "string",
                        },
                        "enums": {
                            "decision": ["allow", "deny", "escalate"],
                            "reason_code": [
                                "action.allowed",
                                "action.forbidden",
                                "approval.required",
                            ],
                        },
                    },
                }
            )
        )
        wrong = Proposal(
            result={
                "decision": "allow",
                "reason_code": "allowed pending approval",
            }
        )

        spec_result = ResultSpecGate().evaluate(contract, wrong)
        decision_result = PolicyDecisionGate().evaluate(contract, wrong)
        reason_result = PolicyReasonCodeGate().evaluate(contract, wrong)

        self.assertEqual(
            spec_result.failure_code,
            GateFailureCode.RESULT_ENUM_INVALID,
        )
        self.assertEqual(
            decision_result.failure_code,
            GateFailureCode.POLICY_DECISION_MISMATCH,
        )
        self.assertIn("'escalate'", decision_result.reason)
        self.assertEqual(
            reason_result.failure_code,
            GateFailureCode.POLICY_REASON_CODE_MISMATCH,
        )
        self.assertIn("'approval.required'", reason_result.reason)

        correct = Proposal(
            result={
                "decision": "escalate",
                "reason_code": "approval.required",
            }
        )
        self.assertTrue(ResultSpecGate().evaluate(contract, correct).passed)
        self.assertTrue(PolicyDecisionGate().evaluate(contract, correct).passed)
        self.assertTrue(PolicyReasonCodeGate().evaluate(contract, correct).passed)

    def test_result_spec_failure_codes_are_stable(self):
        base_inputs = {
            "result_spec": {
                "required_keys": ["decision"],
                "additional_keys": False,
                "types": {"decision": "string"},
                "enums": {"decision": ["allow", "deny"]},
            }
        }
        contract = TaskContract.from_dict(valid_contract(inputs=base_inputs))
        gate = ResultSpecGate()

        cases = (
            (
                TaskContract.from_dict(valid_contract(inputs={"result_spec": {}})),
                Proposal(result={}),
                GateFailureCode.RESULT_SPEC_INVALID,
            ),
            (
                contract,
                Proposal(result={}),
                GateFailureCode.RESULT_KEYS_MISMATCH,
            ),
            (
                contract,
                Proposal(result={"decision": 1}),
                GateFailureCode.RESULT_TYPE_MISMATCH,
            ),
            (
                contract,
                Proposal(result={"decision": "escalate"}),
                GateFailureCode.RESULT_ENUM_INVALID,
            ),
        )
        for case_contract, proposal, code in cases:
            with self.subTest(code=code):
                result = gate.evaluate(case_contract, proposal)
                self.assertFalse(result.passed)
                self.assertEqual(result.failure_code, code)


    def test_all_builtin_failure_codes_are_stable(self):
        forbidden_contract = TaskContract.from_dict(
            valid_contract(
                forbidden_actions=["shell.execute"],
            )
        )

        results = (
            (
                ActionPolicyGate().evaluate(
                    forbidden_contract,
                    Proposal(
                        result={},
                        requested_actions=("shell.execute",),
                    ),
                ),
                GateFailureCode.ACTION_FORBIDDEN,
                (),
            ),
            (
                ActionPolicyGate().evaluate(
                    self.contract,
                    Proposal(
                        result={},
                        requested_actions=("shell.execute",),
                    ),
                ),
                GateFailureCode.ACTION_NOT_ALLOWLISTED,
                (),
            ),
            (
                ClaimEvidenceGate().evaluate(
                    self.contract,
                    Proposal(
                        result={},
                        evidence=(self.evidence, self.evidence),
                    ),
                ),
                GateFailureCode.EVIDENCE_DUPLICATE_ID,
                ("source-1",),
            ),
            (
                ClaimEvidenceGate().evaluate(
                    self.contract,
                    Proposal(
                        result={},
                        claims=(Claim("Missing", ("missing",)),),
                    ),
                ),
                GateFailureCode.EVIDENCE_REFERENCE_MISSING,
                ("missing",),
            ),
            (
                ClaimEvidenceGate().evaluate(
                    self.contract,
                    Proposal(
                        result={},
                        claims=(Claim("Unsupported", ()),),
                    ),
                ),
                GateFailureCode.EVIDENCE_CLAIM_UNSUPPORTED,
                (),
            ),
            (
                RequiredEvidenceGate().evaluate(
                    self.contract,
                    Proposal(result={}),
                ),
                GateFailureCode.EVIDENCE_REQUIRED_MISSING,
                ("source-1",),
            ),
            (
                ResultEqualsGate({"expected": True}).evaluate(
                    self.contract,
                    Proposal(result={"expected": False}),
                ),
                GateFailureCode.RESULT_MISMATCH,
                (),
            ),
        )

        for result, expected_code, expected_evidence in results:
            with self.subTest(code=expected_code):
                self.assertFalse(result.passed)
                self.assertEqual(
                    result.failure_code,
                    expected_code,
                )
                feedback = result.to_feedback()
                self.assertEqual(feedback.code, expected_code)
                self.assertEqual(
                    feedback.evidence_ids,
                    expected_evidence,
                )

    def test_gate_result_enforces_code_invariants(self):
        with self.assertRaises(ValueError):
            GateResult(
                "fixture",
                False,
                "Failure without code",
            )

        with self.assertRaises(ValueError):
            GateResult(
                "fixture",
                True,
                "Passing result",
                failure_code=GateFailureCode.RESULT_MISMATCH,
            )

        passing = GateResult(
            "fixture",
            True,
            "Passing result",
        )
        with self.assertRaises(ValueError):
            passing.to_feedback()

    def test_gate_result_checkpoint_serialization_round_trips(self):
        result = GateResult(
            "fixture",
            False,
            "Fixture failed",
            ("source-1",),
            failure_code=GateFailureCode.EVIDENCE_REQUIRED_MISSING,
        )

        restored = GateResult.from_dict(result.to_dict())

        self.assertEqual(restored, result)
        with self.assertRaisesRegex(ValueError, "unknown gate result"):
            GateResult.from_dict({**result.to_dict(), "unexpected": True})

if __name__ == "__main__":
    unittest.main()
