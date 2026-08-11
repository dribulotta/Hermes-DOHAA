import unittest
from dataclasses import dataclass

from hermes_dohaa.assurance.gates import GateResult, ResultEqualsGate
from hermes_dohaa.controller.identity import (
    ControlPlaneIdentity,
    ControlPlaneIdentityError,
    capture_control_plane_identity,
)


@dataclass(frozen=True, slots=True)
class ConfigurablePassGate:
    revision: str
    name: str = "fixture"

    def evaluate(self, contract, proposal):
        del contract, proposal
        return GateResult(self.name, True, "Fixture passed")


@dataclass(frozen=True, slots=True)
class AlternatePassGate:
    revision: str
    name: str = "fixture"

    def evaluate(self, contract, proposal):
        del contract, proposal
        return GateResult(self.name, True, "Alternate fixture passed")


@dataclass(frozen=True, slots=True)
class NonJsonGate:
    opaque: object
    name: str = "fixture"

    def evaluate(self, contract, proposal):
        del contract, proposal
        return GateResult(self.name, True, "Fixture passed")


class ControlPlaneIdentityTests(unittest.TestCase):
    def test_identity_is_deterministic_and_round_trips(self):
        gates = (ResultEqualsGate({"expected": True}),)

        first = capture_control_plane_identity(gates)
        second = capture_control_plane_identity(gates)
        restored = ControlPlaneIdentity.from_dict(first.to_dict())

        self.assertEqual(first, second)
        self.assertEqual(restored, first)
        self.assertEqual(len(first.sha256), 64)
        component_names = {item.name for item in first.components}
        self.assertIn(
            "hermes_dohaa.assurance.result_spec",
            component_names,
        )
        self.assertIn(
            "hermes_dohaa.assurance.semantic_assertions",
            component_names,
        )
        self.assertIn(
            "hermes_dohaa.controller.semantic_repair",
            component_names,
        )
        with self.assertRaises(TypeError):
            first.gates[0].configuration["expected"] = False

    def test_identity_changes_with_gate_configuration_or_source(self):
        baseline = capture_control_plane_identity(
            (ConfigurablePassGate("one"),)
        )
        changed_configuration = capture_control_plane_identity(
            (ConfigurablePassGate("two"),)
        )
        changed_source = capture_control_plane_identity(
            (AlternatePassGate("one"),)
        )

        self.assertNotEqual(
            baseline.sha256,
            changed_configuration.sha256,
        )
        self.assertNotEqual(baseline.sha256, changed_source.sha256)

    def test_identity_rejects_tampering_and_non_json_gate_state(self):
        identity = capture_control_plane_identity(
            (ConfigurablePassGate("one"),)
        ).to_dict()
        identity["gates"][0]["configuration"]["revision"] = "modified"

        with self.assertRaisesRegex(ValueError, "does not match"):
            ControlPlaneIdentity.from_dict(identity)
        with self.assertRaises(ControlPlaneIdentityError):
            capture_control_plane_identity((NonJsonGate(object()),))


if __name__ == "__main__":
    unittest.main()
