import unittest

from hermes_dohaa.assurance.gates import (
    ActionPolicyGate,
    ClaimEvidenceGate,
    GateFailureCode,
    GateResult,
    RequiredEvidenceGate,
    ResultEqualsGate,
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

if __name__ == "__main__":
    unittest.main()
