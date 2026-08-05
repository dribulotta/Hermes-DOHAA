import unittest

from hermes_dohaa.assurance.gates import ActionPolicyGate, ClaimEvidenceGate, RequiredEvidenceGate
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

    def test_claim_gate_rejects_missing_reference(self):
        proposal = Proposal(result={}, claims=(Claim("Unsupported", ("missing",)),))
        result = ClaimEvidenceGate().evaluate(self.contract, proposal)
        self.assertFalse(result.passed)
        self.assertIn("missing evidence", result.reason)


if __name__ == "__main__":
    unittest.main()
