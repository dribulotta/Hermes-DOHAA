import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import check_project


class ProjectValidationTests(unittest.TestCase):
    def test_failed_process_is_recorded_with_its_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = check_project.run_check(
                "failure", ["-c", "print('diagnostic'); raise SystemExit(7)"], output, 10,
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["returncode"], 7)
            self.assertIn("diagnostic", (output / "failure.log").read_text())

    def test_timeout_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(check_project.subprocess, "run", side_effect=subprocess.TimeoutExpired("check", 1)):
                result = check_project.run_check("timeout", [], Path(directory), 1)
            self.assertEqual(result["status"], "timed_out")
            self.assertIsNone(result["returncode"])

    def test_failed_check_does_not_hide_later_checks_and_summary_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report"
            selected = [("bad", ["-c", "raise SystemExit(2)"]), ("good", ["-c", "print('ok')"])]
            with patch.object(check_project, "checks", return_value=selected), contextlib.redirect_stdout(io.StringIO()):
                result = check_project.main(["--output", str(output)])
            self.assertEqual(result, 1)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["status"], "failed")
            self.assertEqual([item["status"] for item in summary["checks"]], ["failed", "passed"])
            self.assertFalse(summary["protected_evaluation_selected"])
            self.assertFalse(summary["live_runtime_selected"])

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            sentinel = output / "summary.json"
            sentinel.write_text("original")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                check_project.main(["--output", str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(sentinel.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
