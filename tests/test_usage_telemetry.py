import io
import json
import socket
import unittest
from unittest.mock import Mock, patch

from hermes_dohaa.runtime.hermes_api import HermesApiError, HermesApiRuntime
from hermes_dohaa.runtime.usage import (
    normalize_response_usage,
    summarize_usage,
)


def response_bytes(usage_marker):
    payload = {
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
        ]
    }
    if usage_marker is not _ABSENT:
        payload["usage"] = usage_marker
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


_ABSENT = object()


class UsageTelemetryTests(unittest.TestCase):
    def test_normalizes_documented_usage_shapes(self):
        chat = normalize_response_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "ignored_provider_field": 99,
            }
        )
        responses = normalize_response_usage(
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        )

        self.assertEqual(chat["status"], "reported")
        self.assertEqual(chat["shape"], "openai_chat_completions")
        self.assertEqual(responses["status"], "reported")
        self.assertEqual(responses["shape"], "openai_responses")
        self.assertEqual(responses["prompt_tokens"], 10)
        self.assertEqual(responses["completion_tokens"], 2)
        self.assertNotIn("input_tokens", responses)

    def test_absent_and_invalid_usage_are_explicit_and_value_free(self):
        missing = normalize_response_usage(None)
        invalid_cases = (
            {},
            {"prompt_tokens": 10.5, "completion_tokens": 2, "total_tokens": 12.5},
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 11},
            {
                "prompt_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
        )

        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["reason_code"], "usage.missing")
        for raw in invalid_cases:
            with self.subTest(raw=raw):
                record = normalize_response_usage(raw)
                self.assertEqual(record["status"], "invalid")
                self.assertEqual(record["reason_code"], "usage.invalid")
                self.assertNotIn("total_tokens", record)

    def test_runtime_records_one_safe_observation_per_call(self):
        contract = Mock()
        contract.to_dict.return_value = {"contract_id": "safe"}
        runtime = HermesApiRuntime()

        with patch(
            "urllib.request.urlopen",
            return_value=response_bytes(_ABSENT),
        ):
            runtime.propose(contract, ())
        with patch(
            "urllib.request.urlopen",
            return_value=response_bytes({"total_tokens": 12}),
        ):
            runtime.propose(contract, ())
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            with self.assertRaises(HermesApiError):
                runtime.propose(contract, ())

        self.assertEqual(
            [record["status"] for record in runtime.usage_records],
            ["missing", "invalid", "unavailable"],
        )
        self.assertEqual(
            [record["call_index"] for record in runtime.usage_records],
            [1, 2, 3],
        )
        self.assertEqual(
            runtime.usage_records[-1]["details"]["runtime_failure_code"],
            "response.timeout",
        )

    def test_summary_is_fail_closed_and_accepts_legacy_totals(self):
        records = [
            {"total_tokens": 10},
            normalize_response_usage(None),
            normalize_response_usage({"total_tokens": 2}),
        ]
        summary = summarize_usage(records, 4)

        self.assertEqual(summary["reported_calls"], 1)
        self.assertEqual(summary["missing_calls"], 1)
        self.assertEqual(summary["invalid_calls"], 1)
        self.assertEqual(summary["unobserved_calls"], 1)
        self.assertEqual(summary["reported_total_tokens"], 10)
        self.assertFalse(summary["complete"])


if __name__ == "__main__":
    unittest.main()
