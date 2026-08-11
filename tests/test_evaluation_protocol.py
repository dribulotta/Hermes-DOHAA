import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hermes_dohaa.cli import main
from hermes_dohaa.evaluation import (
    EvaluationProtocol,
    EvaluationProtocolError,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO_ROOT / "examples/multimodel-evaluation-protocol.json"
SCHEMA_PATH = REPO_ROOT / "schemas/evaluation-protocol.schema.json"


def protocol_dict():
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


class EvaluationProtocolTests(unittest.TestCase):
    def test_public_protocol_is_canonical_and_detached(self):
        raw = protocol_dict()
        protocol = EvaluationProtocol.from_dict(raw)
        first_hash = protocol.sha256()

        raw["suite_policy"]["domain_counts"]["evidence_synthesis"] = 999
        exported = protocol.to_dict()
        exported["suite_policy"]["domain_counts"]["evidence_synthesis"] = 998

        self.assertEqual(protocol.protocol_id, "multimodel-generalization-v1")
        self.assertEqual(len(protocol.model_slots), 3)
        self.assertEqual(protocol.suite_policy["case_count"], 48)
        self.assertEqual(
            protocol.suite_policy["domain_counts"]["evidence_synthesis"],
            12,
        )
        self.assertEqual(len(first_hash), 64)
        self.assertEqual(
            EvaluationProtocol.from_dict(protocol.to_dict()).sha256(),
            first_hash,
        )
        with self.assertRaises(TypeError):
            protocol.suite_policy["domain_counts"]["evidence_synthesis"] = 1

    def test_protocol_rejects_unknown_fields_and_unsafe_changes(self):
        mutations = []

        unknown = protocol_dict()
        unknown["unregistered"] = True
        mutations.append((unknown, "unknown evaluation protocol fields"))

        conditions = protocol_dict()
        conditions["conditions"].reverse()
        mutations.append((conditions, "conditions must be"))

        duplicate_slot = protocol_dict()
        duplicate_slot["model_slots"][1]["slot_id"] = duplicate_slot[
            "model_slots"
        ][0]["slot_id"]
        mutations.append((duplicate_slot, "model slot IDs must be unique"))

        model_count = protocol_dict()
        model_count["model_policy"]["model_count"] = 4
        mutations.append((model_count, "model_count"))

        reused = protocol_dict()
        reused["suite_policy"]["reuse_prior_holdouts"] = True
        mutations.append((reused, "reuse_prior_holdouts must be false"))

        bad_count = protocol_dict()
        bad_count["suite_policy"]["case_count"] = 47
        mutations.append((bad_count, "case_count must equal"))

        discarded_failures = protocol_dict()
        discarded_failures["analysis_plan"]["runtime_failures"] = "discard"
        mutations.append((discarded_failures, "runtime_failures must be"))

        relaxed_success = protocol_dict()
        relaxed_success["success_criteria"][
            "require_primary_p_below_alpha"
        ] = False
        mutations.append((relaxed_success, "require_primary_p_below_alpha"))

        bad_temperature = protocol_dict()
        bad_temperature["execution_policy"]["temperature"] = 2.1
        mutations.append((bad_temperature, "temperature must be"))

        for raw, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(EvaluationProtocolError, message):
                    EvaluationProtocol.from_dict(raw)

    def test_cli_validates_and_hashes_protocol(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                ["validate-evaluation-protocol", str(PROTOCOL_PATH)]
            )
        payload = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["model_slots"], 3)
        self.assertEqual(payload["case_count"], 48)
        self.assertEqual(
            payload["protocol_sha256"],
            EvaluationProtocol.from_json_file(PROTOCOL_PATH).sha256(),
        )

    def test_cli_rejects_invalid_protocol(self):
        raw = protocol_dict()
        raw["model_policy"]["allow_post_freeze_substitution"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-protocol.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["validate-evaluation-protocol", str(path)]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error_type"], "EvaluationProtocolError")
        self.assertNotIn("protocol_sha256", payload)

    def test_schema_and_example_declare_the_same_root_fields(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        example = protocol_dict()

        self.assertEqual(set(schema["required"]), set(example))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["status"]["const"], "preregistered")
        self.assertEqual(
            schema["properties"]["analysis_plan"]["properties"][
                "runtime_failures"
            ]["const"],
            "count_as_failures",
        )


if __name__ == "__main__":
    unittest.main()
