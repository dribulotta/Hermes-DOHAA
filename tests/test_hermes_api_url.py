import unittest

from hermes_dohaa.runtime.hermes_api import HermesApiRuntime


class HermesApiUrlTests(unittest.TestCase):
    def test_chat_completions_url_accepts_root_and_v1_forms(self):
        expected = (
            "http://10.10.10.106:8642"
            "/v1/chat/completions"
        )

        variants = (
            "http://10.10.10.106:8642",
            "http://10.10.10.106:8642/",
            "http://10.10.10.106:8642/v1",
            "http://10.10.10.106:8642/v1/",
        )

        for base_url in variants:
            with self.subTest(base_url=base_url):
                runtime = HermesApiRuntime(
                    base_url=base_url,
                )
                self.assertEqual(
                    runtime._chat_completions_url(),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
