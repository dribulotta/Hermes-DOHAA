import unittest

from hermes_dohaa.runtime.hermes_api import HermesApiError, parse_proposal_content


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


if __name__ == "__main__":
    unittest.main()
