from pathlib import Path
import tempfile
import unittest

from tools.run_document_stream_demo import run_demo


class DemoTests(unittest.TestCase):
    def test_public_controls_and_no_output_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo"
            result = run_demo(output)
            self.assertTrue(result["passed"])
            self.assertEqual(result["native_model_calls"], 0)
            self.assertFalse(result["architectural_comparison"])
            self.assertEqual(result["unique_delivery_records"], 6)
            self.assertEqual(len(result["reference_controls"]), 6)
            self.assertEqual(result["negative_controls_rejected"], 2)
            retained = (output / "summary.json").read_bytes()
            with self.assertRaises(FileExistsError):
                run_demo(output)
            self.assertEqual((output / "summary.json").read_bytes(), retained)


if __name__ == "__main__":
    unittest.main()
