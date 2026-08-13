import io
import hashlib
import json
import socket
import urllib.error
import unittest
from unittest.mock import Mock, patch

from hermes_dohaa.runtime.base import VerifierFeedback
from hermes_dohaa.runtime.hermes_api import (
    HermesApiError,
    HermesApiRuntime,
    parse_proposal_content,
)


class HermesApiTests(unittest.TestCase):
    def test_structured_error_details_are_cloned(self):
        details = {"stage": "test", "nested": {"count": 1}}
        error = HermesApiError("response.shape_invalid", "safe", details)
        details["nested"]["count"] = 2
        serialized = error.to_dict()
        serialized["details"]["nested"]["count"] = 3
        self.assertEqual(error.details["nested"]["count"], 1)

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
        content = 'secret prose {"result": true}'
        with self.assertRaises(HermesApiError) as caught:
            parse_proposal_content(content)
        error = caught.exception
        self.assertEqual(error.code, "proposal.content_non_json")
        self.assertEqual(error.details["character_length"], len(content))
        self.assertEqual(error.details["sha256"], hashlib.sha256(content.encode()).hexdigest())
        self.assertNotIn(content, json.dumps(error.to_dict()))

    def test_proposal_schema_failure_has_distinct_code(self):
        with self.assertRaises(HermesApiError) as caught:
            parse_proposal_content("[]")
        self.assertEqual(caught.exception.code, "proposal.schema_invalid")

    def test_runtime_classifies_transport_response_and_shape_failures(self):
        contract = Mock()
        contract.to_dict.return_value = {"contract_id": "safe"}
        runtime = HermesApiRuntime()
        cases = (
            (TimeoutError(), "response.timeout"),
            (urllib.error.URLError(socket.timeout()), "response.timeout"),
            (urllib.error.URLError("refused"), "response.connection_failed"),
            (urllib.error.HTTPError("https://safe.invalid", 503, "no", {}, None), "response.http_error"),
        )
        for raised, code in cases:
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=raised):
                with self.assertRaises(HermesApiError) as caught:
                    runtime.propose(contract, ())
                self.assertEqual(caught.exception.code, code)

        for payload, code in ((b"not json", "response.json_invalid"), (b"{}", "response.shape_invalid")):
            response = io.BytesIO(payload)
            with self.subTest(code=code), patch("urllib.request.urlopen", return_value=response):
                with self.assertRaises(HermesApiError) as caught:
                    runtime.propose(contract, ())
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.details["byte_length"], len(payload))
                if payload == b"not json":
                    self.assertNotIn("not json", json.dumps(caught.exception.to_dict()))

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
                    "status": "reported",
                    "source": "response.usage",
                    "shape": "openai_chat_completions",
                    "call_index": 1,
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
