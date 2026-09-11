"""New synthetic source and claim regressions; no model or protected data."""
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hermes_dohaa.assurance.gates import ClaimEvidenceGate
from hermes_dohaa.cli import _contract_gates, _validate_contract_gate_inputs
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController, RunStatus
from hermes_dohaa.controller import identity
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.base import Claim, EvidenceItem, Proposal
from test_contracts import valid_contract


def fixture():
    evidence = EvidenceItem.create("source-1", "artifact", "synthetic-inventory", {"stock": 29})
    policy = {
        "schema_version": "1.0",
        "sources": [{"evidence_id": evidence.evidence_id, "kind": evidence.kind,
                     "source": evidence.source, "sha256": evidence.sha256}],
        "claims": [{"evidence_id": "source-1", "evidence_pointer": "/stock",
                    "result_pointer": "/stock", "prefix": "Stock is ", "suffix": "."}],
    }
    proposal = Proposal({"stock": 29}, (Claim("Stock is 29.", ("source-1",)),), (evidence,))
    return policy, proposal


def task(policy, *, semantic=False, approval=False):
    inputs = {"evidence_policy": policy}
    if semantic:
        inputs.update({"authoritative_stock": 29, "semantic_assertions": [{
            "assertion_id": "stock.matches.input", "operator": "equals",
            "left": {"op": "ref", "source": "result", "pointer": "/stock"},
            "right": {"op": "ref", "source": "inputs", "pointer": "/authoritative_stock"},
        }]})
    return TaskContract.from_dict(valid_contract(
        inputs=inputs, max_attempts=1, allowed_actions=[], forbidden_actions=["shell.execute"],
        requires_human_approval=approval,
    ))


class FixedRuntime:
    def __init__(self, proposal):
        self.proposal = proposal
    def propose(self, contract, feedback):
        return self.proposal


class EvidencePolicyTests(unittest.TestCase):
    def evaluate(self, policy, proposal):
        return ClaimEvidenceGate().evaluate(task(policy), proposal)

    def test_valid_bound_source_and_claim_pass(self):
        policy, proposal = fixture()
        self.assertTrue(self.evaluate(policy, proposal).passed)

    def test_rehashed_fabrication_and_substituted_metadata_fail(self):
        policy, proposal = fixture()
        for source in (
            EvidenceItem.create("source-1", "artifact", "synthetic-inventory", {"stock": 903}),
            EvidenceItem.create("source-1", "artifact", "synthetic-substitute", {"stock": 29}),
            EvidenceItem.create("source-1", "different-kind", "synthetic-inventory", {"stock": 29}),
        ):
            with self.subTest(source=source.source, kind=source.kind):
                result = self.evaluate(policy, replace(proposal, evidence=(source,)))
                self.assertFalse(result.passed)
                self.assertEqual(result.failure_code, "evidence.binding_mismatch")

    def test_direct_dataclass_digest_is_recomputed(self):
        policy, proposal = fixture()
        forged = replace(proposal.evidence[0], content={"stock": 903})
        self.assertFalse(self.evaluate(policy, replace(proposal, evidence=(forged,))).passed)

    def test_false_claim_and_result_mismatch_fail(self):
        policy, proposal = fixture()
        for candidate in (
            replace(proposal, claims=(Claim("Stock is 903.", ("source-1",)),)),
            replace(proposal, result={"stock": 903}),
            replace(proposal, result={"stock": "29"}),
            replace(proposal, result={"stock": True}),
            replace(proposal, result={}),
        ):
            with self.subTest(candidate=candidate.result):
                result = self.evaluate(policy, candidate)
                self.assertFalse(result.passed)
                self.assertEqual(result.failure_code, "evidence.claim_binding_mismatch")

    def test_missing_extra_and_duplicated_claims_cannot_bypass(self):
        policy, proposal = fixture()
        for claims in ((), proposal.claims * 2,
                       proposal.claims + (Claim("Another claim.", ("source-1",)),),
                       (Claim("Stock is 29.", ("source-1", "source-1")),)):
            with self.subTest(claims=claims):
                self.assertFalse(self.evaluate(policy, replace(proposal, claims=claims)).passed)

    def test_all_committed_sources_required_and_extras_forbidden(self):
        policy, proposal = fixture()
        extra = EvidenceItem.create("extra", "artifact", "extra-source", {"value": 1})
        self.assertFalse(self.evaluate(policy, replace(proposal, evidence=proposal.evidence + (extra,))).passed)
        policy["sources"].append({"evidence_id": "extra", "kind": extra.kind,
                                  "source": extra.source, "sha256": extra.sha256})
        self.assertFalse(self.evaluate(policy, proposal).passed)

    def test_empty_or_malformed_policy_never_silently_disables_verification(self):
        valid, proposal = fixture()
        malformed = [None, {}, [], "off", {**valid, "schema_version": "2.0"},
                     {**valid, "unknown": True}, {**valid, "sources": []},
                     {**valid, "claims": []}, {**valid, "claims": valid["claims"] * 33},
                     {**valid, "sources": valid["sources"] * 33}]
        for key in ("sources", "claims", "schema_version"):
            malformed.append({k: v for k, v in valid.items() if k != key})
        for policy in malformed:
            with self.subTest(policy=policy):
                result = self.evaluate(policy, proposal)
                self.assertFalse(result.passed)
                self.assertEqual(result.failure_code, "evidence.policy_invalid")

    def test_malformed_source_and_claim_definitions_are_rejected(self):
        valid, proposal = fixture()
        mutations = [
            ("sources", "sha256", "x" * 64), ("sources", "sha256", "A" * 64),
            ("sources", "source", ""), ("sources", "kind", " " * 2),
            ("sources", "unknown", True), ("claims", "unknown", True),
            ("claims", "evidence_id", "missing"), ("claims", "prefix", 1),
            ("claims", "prefix", "x" * 513), ("claims", "evidence_pointer", "stock"),
            ("claims", "result_pointer", "/bad~2escape"),
            ("claims", "result_pointer", "/" + "x" * 1025),
        ]
        for group, key, value in mutations:
            policy = copy.deepcopy(valid)
            policy[group][0][key] = value
            with self.subTest(group=group, key=key, value=value):
                self.assertEqual(self.evaluate(policy, proposal).failure_code, "evidence.policy_invalid")
        for group in ("sources", "claims"):
            policy = copy.deepcopy(valid)
            policy[group] *= 2
            self.assertEqual(self.evaluate(policy, proposal).failure_code, "evidence.policy_invalid")

    def test_claim_binding_requires_matching_result_after_repair(self):
        policy, proposal = fixture()
        false_claim = Claim("Stock is 903.", ("source-1",))
        for fabricated in (False, True):
            source = proposal.evidence[0] if not fabricated else EvidenceItem.create(
                "source-1", "artifact", "synthetic-inventory", {"stock": 903})
            candidate = replace(proposal, result={"stock": 903}, claims=(false_claim,), evidence=(source,))
            before = candidate.to_dict()
            contract = task(policy, semantic=True)
            with EvidenceLedger() as ledger:
                result = DohaaController(FixedRuntime(candidate), _contract_gates(contract), ledger).run(contract)
                self.assertNotEqual(result.status, RunStatus.SUCCEEDED)
                self.assertEqual(result.proposal.claims, (false_claim,))
                self.assertEqual(result.proposal.evidence, (source,))
                self.assertTrue(ledger.verify_chain())
            self.assertEqual(candidate.to_dict(), before)

    def test_repair_with_correct_bound_claim_can_still_succeed(self):
        policy, proposal = fixture()
        contract = task(policy, semantic=True)
        candidate = replace(proposal, result={"stock": 903})
        with EvidenceLedger() as ledger:
            result = DohaaController(FixedRuntime(candidate), _contract_gates(contract), ledger).run(contract)
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            self.assertEqual(result.proposal.result, {"stock": 29})
            self.assertEqual(result.proposal.claims, proposal.claims)
            self.assertEqual(result.proposal.evidence, proposal.evidence)
            self.assertTrue(ledger.verify_chain())

    def test_cli_contract_validation_rejects_invalid_policy(self):
        policy, _ = fixture()
        _validate_contract_gate_inputs(task(policy))
        for invalid in (None, {}, {**policy, "claims": []}):
            with self.assertRaisesRegex(ValueError, "evidence_policy"):
                _validate_contract_gate_inputs(task(invalid))

    def test_failure_feedback_does_not_echo_source_or_expected_values(self):
        policy, proposal = fixture()
        marker = "synthetic-sensitive-marker"
        evidence = EvidenceItem.create("source-1", "artifact", marker, {"stock": marker})
        policy["sources"][0].update(source=marker, sha256=evidence.sha256)
        candidate = replace(proposal, evidence=(evidence,), result={"stock": "wrong"})
        result = self.evaluate(policy, candidate)
        self.assertFalse(result.passed)
        self.assertNotIn(marker, json.dumps(result.to_feedback().to_dict()))

    def test_unconfigured_contract_retains_structural_only_semantics(self):
        _, proposal = fixture()
        legacy = TaskContract.from_dict(valid_contract())
        false = replace(proposal, claims=(Claim("Stock is 903.", ("source-1",)),))
        self.assertTrue(ClaimEvidenceGate().evaluate(legacy, false).passed)

    def test_root_escaped_array_pointers_and_canonical_json_values(self):
        values = (None, True, 1.25, "Espa\u00f1ol \U0001f4e6", [2, 1], {"b": 2, "a": 1})
        for value in values:
            with self.subTest(value=value):
                policy, proposal = fixture()
                source = EvidenceItem.create("source-1", "artifact", "synthetic-inventory",
                                             {"a/b~c": [value]})
                policy["sources"][0]["sha256"] = source.sha256
                policy["claims"][0].update(evidence_pointer="/a~1b~0c/0", result_pointer="")
                statement = "Stock is " + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "."
                candidate = replace(proposal, result=value, evidence=(source,),
                                    claims=(Claim(statement, ("source-1",)),))
                self.assertTrue(self.evaluate(policy, candidate).passed)
                policy["claims"][0]["evidence_pointer"] = "/a~1b~0c/01"
                self.assertFalse(self.evaluate(policy, candidate).passed)

    def test_multiple_sources_and_claims_allow_order_changes_not_wrong_references(self):
        policy, proposal = fixture()
        second = EvidenceItem.create("source-2", "artifact", "synthetic-region", {"region": "west"})
        policy["sources"].append({"evidence_id": "source-2", "kind": second.kind,
                                  "source": second.source, "sha256": second.sha256})
        policy["claims"].append({"evidence_id": "source-2", "evidence_pointer": "/region",
                                 "result_pointer": "/region", "prefix": "Region is ", "suffix": "."})
        region_claim = Claim('Region is "west".', ("source-2",))
        candidate = replace(proposal, result={"stock": 29, "region": "west"},
                            evidence=(second, *proposal.evidence), claims=(region_claim, *proposal.claims))
        self.assertTrue(self.evaluate(policy, candidate).passed)
        wrong = replace(region_claim, evidence_ids=("source-1",))
        self.assertFalse(self.evaluate(policy, replace(candidate, claims=(wrong, *proposal.claims))).passed)

    def test_runtime_cannot_rewrite_controller_owned_commitments(self):
        policy, proposal = fixture()
        forged = EvidenceItem.create("source-1", "artifact", "synthetic-inventory", {"stock": 903})
        candidate = replace(proposal, result={"stock": 903}, evidence=(forged,),
                            claims=(Claim("Stock is 903.", ("source-1",)),))
        class RewritingRuntime:
            def propose(self, contract, feedback):
                contract.inputs["evidence_policy"]["sources"][0]["sha256"] = forged.sha256
                return candidate
        contract = task(policy)
        with EvidenceLedger() as ledger:
            result = DohaaController(RewritingRuntime(), _contract_gates(contract), ledger).run(contract)
            self.assertNotEqual(result.status, RunStatus.SUCCEEDED)
            self.assertTrue(ledger.verify_chain())
        self.assertEqual(contract.inputs["evidence_policy"]["sources"][0]["sha256"], proposal.evidence[0].sha256)

    def test_evidence_policy_module_is_part_of_approval_identity(self):
        gates = (ClaimEvidenceGate(),)
        before = identity.capture_control_plane_identity(gates)
        self.assertIn("hermes_dohaa.assurance.evidence_policy", {item.name for item in before.components})
        original = identity._source_sha256
        def changed_source(obj, label):
            return "0" * 64 if label == "hermes_dohaa.assurance.evidence_policy" else original(obj, label)
        with patch.object(identity, "_source_sha256", side_effect=changed_source):
            after = identity.capture_control_plane_identity(gates)
        self.assertNotEqual(before.sha256, after.sha256)

    def test_valid_bound_proposal_still_requires_configured_human_approval(self):
        policy, proposal = fixture()
        contract = task(policy, approval=True)
        with EvidenceLedger() as ledger:
            result = DohaaController(FixedRuntime(proposal), _contract_gates(contract), ledger).run(contract)
            self.assertEqual(result.status, RunStatus.ESCALATED)
            self.assertEqual(result.reason_code, "approval.required")
            self.assertTrue(ledger.verify_chain())

    def test_documented_example_runs_with_bound_evidence_and_preserves_bad_claim(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        contract = TaskContract.from_json_file(examples / "task_contract_bound_evidence.json")
        proposal = Proposal.from_dict(json.loads((examples / "proposal_bound_evidence.json").read_text()))
        _validate_contract_gate_inputs(contract)
        with EvidenceLedger() as ledger:
            result = DohaaController(FixedRuntime(proposal), _contract_gates(contract), ledger).run(contract)
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            self.assertTrue(ledger.verify_chain())
        false = replace(proposal, claims=(Claim("Stock is 903.", ("source-1",)),))
        with EvidenceLedger() as ledger:
            result = DohaaController(FixedRuntime(false), _contract_gates(contract), ledger).run(contract)
            self.assertNotEqual(result.status, RunStatus.SUCCEEDED)
            self.assertEqual(result.proposal.claims, false.claims)
            self.assertTrue(ledger.verify_chain())

    def test_distinct_bindings_cannot_require_duplicate_rendered_claims(self):
        policy, proposal = fixture()
        policy["claims"].append({**policy["claims"][0], "result_pointer": "/copy"})
        candidate = replace(proposal, result={"stock": 29, "copy": 29}, claims=proposal.claims * 2)
        self.assertFalse(self.evaluate(policy, candidate).passed)

    def test_maximum_32_sources_and_claims_are_supported(self):
        policy, original = fixture()
        policy.update(sources=[], claims=[])
        evidence, claims = [], []
        for index in range(32):
            source = replace(original.evidence[0], evidence_id=f"source-{index}")
            prefix = f"Stock {index} is "
            policy["sources"].append({"evidence_id": source.evidence_id, "kind": source.kind,
                                      "source": source.source, "sha256": source.sha256})
            policy["claims"].append({"evidence_id": source.evidence_id, "evidence_pointer": "/stock",
                                     "result_pointer": f"/{index}", "prefix": prefix, "suffix": "."})
            evidence.append(source)
            claims.append(Claim(prefix + "29.", (source.evidence_id,)))
        candidate = Proposal([29] * 32, tuple(claims), tuple(evidence))
        self.assertTrue(self.evaluate(policy, candidate).passed)


if __name__ == "__main__":
    unittest.main()
