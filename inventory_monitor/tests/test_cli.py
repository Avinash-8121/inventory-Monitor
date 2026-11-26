"""End-to-end tests: drive the real terminal app through stdin, restart it, and check the files."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_app(data: Path, lines: list[str], *extra: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "app.py"), "--no-color", "--data", str(data),
         "--export-dir", str(data.parent / "exports"), *extra],
        input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    return proc.stdout


class CliJourneyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name) / "inventory.json"
        run_app(self.data, ["0"], "--reset-sample")

    def test_demo_journey(self):
        # Session 1: add a product, feeding it bad input first (blank, junk, negative, decimals, future date).
        out = run_app(self.data, [
            "9", "STN-001", "ab", "ZZZ-001",
            "", "Demo Marker",
            "Stationery",
            "lots", "-4", "2.5", "25",
            "free", "-1", "1.999", "1.50",
            "-2", "10",
            "", "2999-01-01", "",
            "6", "zzz-001", "5", "", "0"])
        for needle in ["already exists", "SKU must be", "Name is required", "must be a whole number",
                       "cannot be negative", "at most 2 decimal", "cannot be in the future", "Added ZZZ-001",
                       "Sold 5 x Demo Marker. 20 left"]:
            self.assertIn(needle, out)

        # Session 2 (fresh process): data survived the restart.
        out = run_app(self.data, ["2", "marker", "", "", "0"])
        self.assertIn("ZZZ-001", out)
        self.assertIn("41 products loaded", out)
        saved = json.loads(self.data.read_text())
        zzz = next(p for p in saved["products"] if p["sku"] == "ZZZ-001")
        self.assertEqual(zzz["quantity"], 20)

    def test_oversell_and_cancel_leave_data_untouched(self):
        before = self.data.read_text()
        out = run_app(self.data, ["6", "STN-001", "99999", "", "6", "STN-001", "/cancel", "0"])
        self.assertIn("Only 120 of STN-001 in stock", out)
        self.assertIn("Cancelled", out)
        self.assertEqual(json.loads(before)["products"], json.loads(self.data.read_text())["products"])

    def test_menu_rejects_junk_and_survives_eof(self):
        out = run_app(self.data, ["banana", "99", "-1"])        # input ends abruptly (EOF)
        self.assertIn("is not on the menu", out)
        self.assertIn("Goodbye", out)

    def test_summary_alerts_import_and_export(self):
        out = run_app(self.data, ["4", "3", "12", "", "", "13", "all", "0"])
        self.assertIn("Executive summary", out)
        self.assertIn("Healthy stock", out)
        self.assertIn("Out of stock", out)
        self.assertIn("6 imported", out)
        self.assertIn("11 rejected", out)
        exports = self.data.parent / "exports"
        self.assertTrue((exports / "import_errors.log").exists())
        suffixes = {p.suffix for p in exports.iterdir()}
        self.assertTrue({".md", ".txt", ".csv", ".log"} <= suffixes)

    def test_corrupted_file_on_launch(self):
        self.data.write_text("{{{ not json", encoding="utf-8")
        out = run_app(self.data, ["0"])
        self.assertIn("not valid JSON", out)
        self.assertTrue(list(self.data.parent.glob("inventory.json.corrupt-*")))


if __name__ == "__main__":
    unittest.main()
