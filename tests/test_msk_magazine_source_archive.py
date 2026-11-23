"""Offline negative-path validation of original-source NSE PDF reconciliation.

Synthetic PDF fixtures test *parsing logic*, not market facts. Only a live
official NSE download can establish the actual external provenance.
"""
import copy
import io
import json
from pathlib import Path
import unittest

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from scripts.msk_magazine_source_archive import (
    SourceEvidenceError, verify_nse_dashboard, _find_named_row,
    _safe_download,
)

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"


def synthetic_pdf(metrics, *, changes=None, date_label="Index Dashboard October 31 2026"):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    c.drawString(20, 810, date_label)
    entries = metrics["market_indices"] + metrics["sectors"]
    for i, row in enumerate(entries):
        name = "Nifty500 Equal Weight" if row["name"] == "Nifty 500 Equal Weight" else row["name"]
        columns = [
            row["m1"],
            row.get("m3", 0),
            row.get("y1", 0),
            1, 1,
            row.get("vol", 1),
            1, 1, 1,
            row.get("pe", 1),
            row.get("pb", 1),
            row.get("dy", 1),
        ]
        if changes and name in changes:
            columns[changes[name][0]] = changes[name][1]
        line = name + " " + " ".join(f"{float(v):.2f}" for v in columns)
        c.drawString(20, 780 - i * 32, line)
    c.showPage()
    c.save()
    return buf.getvalue()


class NSEDashboardSourceArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metrics = json.loads(METRICS.read_text(encoding="utf-8"))

    def test_all_14_named_official_rows_and_columns_reconciled(self):
        data = verify_nse_dashboard(synthetic_pdf(self.metrics), self.metrics)
        self.assertEqual(data["checked_rows"], 14)
        self.assertEqual(len(data["sha256"]), 64)
        self.assertIn("MATCH", data["status"])

    def test_exact_nifty_500_not_confused_with_equal_weight(self):
        bad = synthetic_pdf(self.metrics, changes={"Nifty500 Equal Weight": (0, -4.12)})
        with self.assertRaisesRegex(SourceEvidenceError, "Nifty 500 Equal Weight m1"):
            verify_nse_dashboard(bad, self.metrics)

    def test_wrong_field_rejected_even_when_number_occurs_elsewhere(self):
        # Nifty 50's one-month return is 1.95; placing 1.95 in the three-month
        # field must NOT satisfy the one-month source evidence.
        bad = synthetic_pdf(self.metrics, changes={"Nifty 50": (0, 3.95)})
        with self.assertRaisesRegex(SourceEvidenceError, "Nifty 50 m1"):
            verify_nse_dashboard(bad, self.metrics)

    def test_future_or_other_dashboard_date_rejected(self):
        bad = synthetic_pdf(self.metrics, date_label="Index Dashboard November 30 2026")
        with self.assertRaisesRegex(SourceEvidenceError, "date"):
            verify_nse_dashboard(bad, self.metrics)

    def test_wrong_official_url_rejected_without_fetch(self):
        bad = copy.deepcopy(self.metrics)
        bad["market_source"]["url"] = "https://www.niftyindices.com/Index_Dashboard/Index_Dashboard_NOV2026.pdf"
        with self.assertRaisesRegex(SourceEvidenceError, "URL"):
            verify_nse_dashboard(synthetic_pdf(self.metrics), bad)

    def test_no_unguarded_network_url(self):
        with self.assertRaisesRegex(SourceEvidenceError, "Unexpected NSE source URL"):
            _safe_download(None, "https://evil.example/Index_Dashboard_OCT2026.pdf")

    def test_missing_named_row_rejected(self):
        with self.assertRaisesRegex(SourceEvidenceError, "dashboard row"):
            _find_named_row(["Nifty 500 3.00 4.00"], "Nifty 50")


if __name__ == "__main__":
    unittest.main()
