import unittest

from hermes_dohaa.contracts.models import ContractError, RiskLevel, TaskContract


def valid_contract(**overrides):
    raw = {
        "schema_version": "1.0",
        "contract_id": "test-1",
        "objective": "Produce a verified result",
        "acceptance_criteria": [
            {
                "criterion_id": "grounded",
                "description": "Result is grounded",
                "required_evidence": ["source-1"],
            }
        ],
        "allowed_actions": ["artifact.read"],
        "forbidden_actions": ["external.publish"],
        "risk_level": "medium",
        "max_attempts": 3,
        "requires_human_approval": False,
    }
    raw.update(overrides)
    return raw


class TaskContractTests(unittest.TestCase):
    def test_valid_contract_is_canonical(self):
        contract = TaskContract.from_dict(valid_contract())
        self.assertEqual(contract.risk_level, RiskLevel.MEDIUM)
        self.assertIn('"contract_id":"test-1"', contract.canonical_json())

    def test_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ContractError, "Unknown task-contract fields"):
            TaskContract.from_dict(valid_contract(typo=True))

    def test_contract_detaches_inputs_from_mutable_source(self):
        source = {"nested": {"value": 1}}
        contract = TaskContract.from_dict(valid_contract(inputs=source))
        source["nested"]["value"] = 999
        self.assertEqual(contract.inputs["nested"]["value"], 1)

    def test_rejects_action_policy_overlap(self):
        with self.assertRaisesRegex(ContractError, "both allowed and forbidden"):
            TaskContract.from_dict(
                valid_contract(
                    allowed_actions=["external.publish"],
                    forbidden_actions=["external.publish"],
                )
            )

    def test_critical_contract_requires_human_approval(self):
        with self.assertRaisesRegex(ContractError, "critical contracts"):
            TaskContract.from_dict(valid_contract(risk_level="critical"))


if __name__ == "__main__":
    unittest.main()
