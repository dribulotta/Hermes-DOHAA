"""Synthetic admission regressions; no model or external API calls."""

import hashlib
import io
import json
import unittest
from unittest.mock import patch

from hermes_dohaa.assurance.gates import ActionPolicyGate
from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.controller.engine import DohaaController, RunStatus
from hermes_dohaa.evidence.ledger import EvidenceLedger
from hermes_dohaa.runtime.hermes_api import (
    HermesApiError, HermesApiRuntime, parse_proposal_content,
)
from test_contracts import valid_contract


VALID = '{"result":{"stock":17},"requested_actions":[]}'
INVALID_VALUES = (
    '{"result":NaN}', '{"result":Infinity}', '{"result":-Infinity}',
    '{"result":1e9999}', '{"result":"\\ud800"}',
    '{"result":{"\\udfff":17}}',
)


def response_bytes(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def contract():
    return TaskContract.from_dict(valid_contract(
        allowed_actions=[], forbidden_actions=["shell.execute"], max_attempts=3,
        acceptance_criteria=[{"criterion_id": "synthetic", "description": "Synthetic check.",
                              "required_evidence": []}],
    ))


class StrictHermesJsonTests(unittest.TestCase):
    def assert_invalid(self, content, code="proposal.content_invalid"):
        with self.assertRaises(HermesApiError) as caught:
            parse_proposal_content(content)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_rejects_duplicate_proposal_keys_at_all_levels_and_orders(self):
        for content in (
            '{"result":1,"result":2}',
            '{"result":{"stock":17,"stock":901}}',
            '{"result":0,"requested_actions":["shell.execute"],"requested_actions":[]}',
            '{"result":0,"requested_actions":[],"requested_actions":["shell.execute"]}',
            '{"result":0,"evidence":[{"evidence_id":"first","evidence_id":"second"}]}',
        ):
            with self.subTest(content=content):
                self.assert_invalid(content)

    def test_rejects_nonfinite_and_unpaired_unicode_values(self):
        for content in INVALID_VALUES:
            with self.subTest(content=content):
                self.assert_invalid(content)

    def test_raw_surrogate_has_safe_stable_diagnostics(self):
        content = '{"result":"synthetic-sensitive-marker' + chr(0xd800) + '"}'
        error = self.assert_invalid(content)
        self.assertEqual(error.details["sha256"], hashlib.sha256(content.encode("utf-8", errors="surrogatepass")).hexdigest())
        self.assertEqual(error.details["byte_encoding"], "utf-8-surrogatepass")
        self.assertNotIn("synthetic-sensitive-marker", json.dumps(error.to_dict()))

    def test_rejects_unpaired_or_unsupported_markdown_fences(self):
        for content in (
            "```json\n" + VALID, "```yaml\n" + VALID + "\n```",
            "```\n" + VALID + "\n```\ntrailing prose", "```json\n```",
        ):
            with self.subTest(content=content):
                self.assert_invalid(content, "proposal.content_non_json")

    def test_preserves_valid_unicode_numbers_and_paired_fences(self):
        value = {"stock": 17, "label": "Espa\u00f1ol \U0001f4e6", "ratio": 1.25, "large": 10**100}
        raw = json.dumps({"result": value}, ensure_ascii=False)
        for content in (raw, "```json\n" + raw + "\n```", "```\n" + raw + "\n```"):
            self.assertEqual(parse_proposal_content(content).result, value)
        self.assertEqual(parse_proposal_content('{"result":"\\ud83d\\udce6"}').result, "\U0001f4e6")

    def test_limits_container_depth_without_rejecting_boundary(self):
        # The root object is depth 1; total container depth is limited to 128.
        for arrays, accept in ((127, True), (128, False), (1200, False)):
            raw = '{"result":' + '[' * arrays + '0' + ']' * arrays + '}'
            if accept:
                parse_proposal_content(raw)
            else:
                self.assert_invalid(raw)

    def test_integer_decoder_limit_is_classified(self):
        self.assert_invalid('{"result":' + '9' * 5000 + '}')

    def test_private_duplicate_key_is_not_echoed_in_diagnostics(self):
        error = self.assert_invalid('{"result":{"synthetic-sensitive-marker":1,"synthetic-sensitive-marker":2}}')
        self.assertNotIn("synthetic-sensitive-marker", json.dumps(error.to_dict()))

    def test_envelope_rejects_duplicate_keys_nonfinite_and_surrogate(self):
        valid_response = response_bytes(VALID).decode()
        duplicate = valid_response[:-1] + ',"choices":[]}'
        nested_duplicate = '{"choices":[{"message":{"content":"first","content":"second"}}]}'
        invalid_payloads = (
            duplicate, nested_duplicate,
            valid_response[:-1] + ',"usage":{"total_tokens":NaN}}',
            valid_response[:-1] + ',"extra":"\\ud800"}',
            '{"extra":' + '[' * 128 + '0' + ']' * 128 + '}',
            '{"extra":' + '9' * 5000 + '}',
        )
        for payload in invalid_payloads:
            runtime = HermesApiRuntime()
            with self.subTest(payload=payload[:80]), patch(
                "urllib.request.urlopen", return_value=io.BytesIO(payload.encode()),
            ):
                with self.assertRaises(HermesApiError) as caught:
                    runtime.propose(contract(), ())
            self.assertEqual(caught.exception.code, "response.json_invalid")
            self.assertEqual(len(runtime.usage_records), 1)
            self.assertEqual(runtime.usage_records[0]["status"], "unavailable")

    def test_controller_escalates_without_retry_or_unhandled_unicode_error(self):
        for content in (*INVALID_VALUES,
                        '{"result":0,"requested_actions":["shell.execute"],"requested_actions":[]}',
                        '"synthetic prose"'):
            runtime = HermesApiRuntime()
            with self.subTest(content=content), EvidenceLedger() as ledger, patch(
                "urllib.request.urlopen", return_value=io.BytesIO(response_bytes(content)),
            ) as request:
                result = DohaaController(runtime, (ActionPolicyGate(),), ledger).run(contract())
                self.assertEqual(result.status, RunStatus.ESCALATED)
                self.assertEqual(result.attempts, 1)
                self.assertIsNone(result.proposal)
                self.assertIsNotNone(result.runtime_error_code)
                self.assertTrue(ledger.verify_chain())
                self.assertEqual(request.call_count, 1)

    def test_envelope_preserves_valid_unicode_and_rejects_invalid_utf8(self):
        value = {"label": "Espa\u00f1ol \U0001f4e6", "ratio": 1.25}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(response_bytes(json.dumps({"result": value})))):
            self.assertEqual(HermesApiRuntime().propose(contract(), ()).result, value)
        invalid = b'{"synthetic-sensitive-marker":"\xff"}'
        with patch("urllib.request.urlopen", return_value=io.BytesIO(invalid)):
            with self.assertRaises(HermesApiError) as caught:
                HermesApiRuntime().propose(contract(), ())
        self.assertEqual(caught.exception.code, "response.json_invalid")
        self.assertEqual(caught.exception.details["sha256"], hashlib.sha256(invalid).hexdigest())
        self.assertNotIn("synthetic-sensitive-marker", json.dumps(caught.exception.to_dict()))


if __name__ == "__main__":
    unittest.main()
