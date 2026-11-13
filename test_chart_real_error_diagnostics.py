"""Chart errors must preserve the script diagnostic, not Yahoo warning noise."""
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from chart_integration import ChartGenerator


class ChartRealFailureDiagnosticTests(unittest.TestCase):
    def test_script_error_takes_priority_over_download_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            generator = ChartGenerator(script_path="gen_smart_stock_chart.py",
                                       output_dir=folder)
            process = subprocess.CompletedProcess(
                args=["python", "gen_smart_stock_chart.py"], returncode=1,
                stdout="SCAN: sample 60/501\n",
                stderr="7 Failed downloads:\nYahoo partial failure\n[ERROR] No verified chart candidate: no fresh bars",
            )
            with patch("chart_integration.subprocess.run", return_value=process):
                ok, message, path = generator.generate()
            self.assertFalse(ok)
            self.assertIsNone(path)
            self.assertIn("No verified chart candidate: no fresh bars", message)
            self.assertNotIn("7 Failed downloads", message)

    def test_missing_stderr_does_not_mask_error(self):
        with tempfile.TemporaryDirectory() as folder:
            generator = ChartGenerator(script_path="gen_smart_stock_chart.py",
                                       output_dir=folder)
            process = subprocess.CompletedProcess(
                args=["python", "gen_smart_stock_chart.py"], returncode=1,
                stdout="", stderr="",
            )
            with patch("chart_integration.subprocess.run", return_value=process):
                ok, message, path = generator.generate()
            self.assertFalse(ok)
            self.assertIn("Unknown chart subprocess error", message)
            self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
