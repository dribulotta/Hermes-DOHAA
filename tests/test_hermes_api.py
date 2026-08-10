import io
import json
import unittest
from unittest.mock import Mock, patch

from hermes_dohaa.runtime.base import VerifierFeedback
from hermes_dohaa.runtime.hermes_api import (
    HermesApiError,
    HermesApiRuntime,
    parse_proposal_content,
)


class HermesApiTests(unittest.TestCase):
    def test_parses_fenced_json_proposal(self):
        proposal = parse_proposal_content(
            """```json
            {
              "result": {"ok": true},
              "claims": [],
              "evidence": [],
              "requested_actions": []
            }
            ```"""
        )
        self.assertEqual(proposal.result, {"ok": True})

    def test_rejects_non_json(self):
        with self.assertRaises(HermesApiError):
            parse_proposal_content("I think it worked")

    def test_runtime_sends_model_reasoning_policy_and_timeout(self):
        response = io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "result": {"ok": True},
                                        "claims": [],
                                        "evidence": [],
                                        "requested_actions": [],
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                }
            ).encode("utf-8")
        )
        contract = Mock()
        contract.to_dict.return_value = {"contract_id": "test-contract"}
        runtime = HermesApiRuntime(
            model="dohaa-runtime",
            reasoning_effort="none",
            timeout_seconds=42.0,
            temperature=0.0,
            top_p=1.0,
            sampling_seed=17,
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            proposal = runtime.propose(
                contract,
                [
                    VerifierFeedback(
                        gate="result_equals",
                        code="result.mismatch",
                        reason="Proposal result does not equal the expected value",
                        evidence_ids=("expected-value",),
                    )
                ],
            )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(proposal.result, {"ok": True})
        self.assertEqual(body["model"], "dohaa-runtime")
        self.assertEqual(
            body["model_options"],
            {"reasoning": {"enabled": False, "effort": "none"}},
        )
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["top_p"], 1.0)
        self.assertEqual(body["seed"], 17)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 42.0)
        self.assertEqual(
            runtime.usage_records,
            [
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                }
            ],
        )
        user_payload = json.loads(body["messages"][1]["content"])
        self.assertEqual(
            user_payload["verifier_feedback"],
            [
                {
                    "gate": "result_equals",
                    "code": "result.mismatch",
                    "reason": (
                        "Proposal result does not equal the expected value"
                    ),
                    "evidence_ids": ["expected-value"],
                }
            ],
        )

    def test_runtime_rejects_invalid_reasoning_effort(self):
        with self.assertRaises(ValueError):
            HermesApiRuntime(reasoning_effort="unbounded")
        with self.assertRaises(ValueError):
            HermesApiRuntime(reasoning_effort=1)  # type: ignore[arg-type]

    def test_runtime_rejects_invalid_sampling_policy(self):
        with self.assertRaises(ValueError):
            HermesApiRuntime(temperature=-0.1)
        with self.assertRaises(ValueError):
            HermesApiRuntime(top_p=0)
        with self.assertRaises(ValueError):
            HermesApiRuntime(sampling_seed=True)


if __name__ == "__main__":
    unittest.main()
