"""Offline primary-source receipt and numeric-context regression tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.msk_magazine_primary_sources import (
    PrimarySourceError, SOURCES, recheck_primary_sources, verify_source_html,
)


TEXTS = {
    "rbi_repo": "Monetary Policy Resolution October 7 2026. The policy repo rate was increased by 25 basis points to 5.50 per cent.",
    "mospi_cpi": "Consumer Price Index October 2026. Headline inflation 6.09%. Food inflation 8.94%.",
    "mospi_iip": "Index of Industrial Production September 2026 quick estimate showed 6.7 percent growth.",
    "tcs_q2": "TCS quarterly consolidated Q2 FY27 filing. Start 01-07-2026 end 30-09-2026. Revenue from operations 73,18,800.00. NSE Symbol TCS.",
    "persistent_q2": "Persistent Q2 FY27 October 15, 2026: USD revenue 487.6M, year over year 20.0%, EBIT 15.0%.",
    "coforge_q2": "Coforge Q2 FY 27 October 23, 2026: Revenue USD 652.2M, growth 40.1%; EBIT margin guidance 16%.",
    "mospi_gdp_calendar": "Quarterly GDP second quarter of 2026-27 is scheduled for 30 November 2026.",
}


def html_for(key, data=None):
    raw = TEXTS[key] if data is None else data
    return ("<html><head><title>Official dated source</title></head><body>" +
            raw + " Official issuing organization and original public financial" +
            " filing; education and informational purposes only. " +
            "</body></html>").encode("utf-8")


class PrimarySourceEvidenceTests(unittest.TestCase):
    def test_all_seven_contextual_source_claims_can_match(self):
        for key, config in SOURCES.items():
            with self.subTest(source=key):
                result = verify_source_html(key, config["url"], html_for(key))
                self.assertEqual(result["key"], key)
                self.assertEqual(len(result["source_sha256"]), 64)

    def test_missing_issuer_growth_rejected(self):
        text = TEXTS["coforge_q2"].replace("40.1%", "14.3%")
        with self.assertRaisesRegex(PrimarySourceError, "coforge_q2"):
            verify_source_html("coforge_q2", SOURCES["coforge_q2"]["url"],
                               html_for("coforge_q2", text))

    def test_tcs_previous_usd_press_release_not_accepted_as_nse_filing(self):
        stale = "TCS Q2 FY27 October 8, 2026. Revenue USD 7,642, growth 2.4% YoY; operating margin 24.0%."
        with self.assertRaisesRegex(PrimarySourceError, "tcs_q2"):
            verify_source_html("tcs_q2", SOURCES["tcs_q2"]["url"],
                               html_for("tcs_q2", stale))

    def test_tcs_wrong_inr_revenue_rejected(self):
        wrong = TEXTS["tcs_q2"].replace("73,18,800.00", "72,18,800.00")
        with self.assertRaisesRegex(PrimarySourceError, "tcs_q2"):
            verify_source_html("tcs_q2", SOURCES["tcs_q2"]["url"],
                               html_for("tcs_q2", wrong))

    def test_unapproved_host_and_url_rejected(self):
        with self.assertRaisesRegex(PrimarySourceError, "URL"):
            verify_source_html("tcs_q2", "https://example.com/tcs",
                               html_for("tcs_q2"))

    def test_script_only_fake_numbers_are_ignored(self):
        body = ('<html><body>Empty official document.' +
                '<script>' + TEXTS["tcs_q2"] + '</script>' +
                ' Genuine company date not supplied in page body.' +
                '</body></html>').encode("utf-8")
        with self.assertRaisesRegex(PrimarySourceError, "tcs_q2"):
            verify_source_html("tcs_q2", SOURCES["tcs_q2"]["url"], body)

    def test_recheck_original_files_and_detect_tamper(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            receipts = []
            for key, config in SOURCES.items():
                body = html_for(key)
                (root / (key + ".html")).write_bytes(body)
                result = verify_source_html(key, config["url"], body)
                result["path"] = key + ".html"
                receipts.append(result)
            (root / "primary_source_ledger.json").write_text(
                json.dumps({"entries": receipts, "sources_checked": len(receipts)}),
                encoding="utf-8")
            self.assertEqual(recheck_primary_sources(root)["sources_checked"], 7)
            (root / "tcs_q2.html").write_bytes(
                html_for("tcs_q2") + b" altered after source receipt")
            with self.assertRaisesRegex(PrimarySourceError, "bytes changed"):
                recheck_primary_sources(root)


if __name__ == "__main__":
    unittest.main()
