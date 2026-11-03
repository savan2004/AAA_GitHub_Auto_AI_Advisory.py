"""Fail-closed offline tests for exact 16-page magazine PDF layout."""
import re
import tempfile
import unittest
from pathlib import Path

from msk_magazine_pdf import (
    PAGE_TITLES, MagazineValidationError, render_magazine,
)

def mock_pack():
    # Clearly synthetic fixture: never publish this as financial research.
    facts = [
        {"statement": "Synthetic fixture statement for the first example citation; never represent it as live market research.",
         "source_url": "https://www.nseindia.com/example", "observed_on": "2026-10-30"},
        {"statement": "Synthetic fixture statement for the second example citation; no financial prices are asserted.",
         "source_url": "https://www.rbi.org.in/example", "observed_on": "2026-10-30"},
        {"statement": "Synthetic fixture statement for the third example citation; the data is strictly not verified.",
         "source_url": "https://www.sebi.gov.in/example", "observed_on": "2026-10-30"},
    ]
    return {
        "issue": "2026-11", "as_of": "2026-10-30", "approved": False,
        "pages": [
            {"summary": "Synthetic fixture content for unit testing only; not financial evidence or approved market publication.",
             "evidence": facts} for _ in PAGE_TITLES
        ],
    }

class LayoutTests(unittest.TestCase):
    def test_preview_produces_exact_sixteen_pages(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "preview.pdf"
            result = render_magazine(None, target, preview=True)
            self.assertEqual(result["pages"], 16)
            self.assertEqual(result["status"], "LAYOUT_PREVIEW")
            raw = target.read_bytes()
            self.assertTrue(raw.startswith(b"%PDF-"))
            self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", raw)), 16)
            self.assertIn(b"NO VERIFIED DATA", raw)

    def test_source_labelled_synthetic_draft_is_not_approved(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "synthetic.pdf"
            result = render_magazine(mock_pack(), target)
            self.assertEqual(result["status"], "UNAPPROVED_RESEARCH_DRAFT")
            raw = target.read_bytes()
            self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", raw)), 16)
            self.assertIn(b"NOT APPROVED", raw)
            # All three full source URLs per research page survive as PDF links.
            self.assertEqual(len(re.findall(rb"/Subtype\s*/Link\b", raw)), 39)
            self.assertIn(b"https://www.nseindia.com/example", raw)
            self.assertIn(b"https://www.rbi.org.in/example", raw)
            self.assertIn(b"https://www.sebi.gov.in/example", raw)

    def test_malformed_non_research_pages_fail_closed(self):
        for index in (0, 1, 15):
            with self.subTest(page=index + 1):
                with tempfile.TemporaryDirectory() as root:
                    data = mock_pack()
                    data["pages"][index] = None
                    target = Path(root) / "invalid.pdf"
                    with self.assertRaisesRegex(MagazineValidationError, f"Page {index + 1}"):
                        render_magazine(data, target)
                    self.assertFalse(target.exists())

    def test_missing_evidence_blocks_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            data = mock_pack()
            data["pages"][4]["evidence"] = []
            target = Path(root) / "withheld.pdf"
            with self.assertRaises(MagazineValidationError):
                render_magazine(data, target)
            self.assertFalse(target.exists())

    def test_future_issue_label_blocks_draft(self):
        with tempfile.TemporaryDirectory() as root:
            data = mock_pack()
            data["issue"] = "2099-12"
            target = Path(root) / "future.pdf"
            with self.assertRaisesRegex(MagazineValidationError, "Future magazine issue"):
                render_magazine(data, target)
            self.assertFalse(target.exists())

    def test_future_and_unapproved_policy(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "withheld.pdf"
            data = mock_pack()
            data["approved"] = True
            with self.assertRaises(MagazineValidationError):
                render_magazine(data, target)
            data["approved"] = False
            data["as_of"] = "2099-01-01"
            with self.assertRaises(MagazineValidationError):
                render_magazine(data, target)

    def test_nonliteral_draft_approval_flags_fail_closed(self):
        for approval in (1, "true", "false", 0, None, [], {}):
            with self.subTest(approval=repr(approval)):
                with tempfile.TemporaryDirectory() as root:
                    data = mock_pack()
                    data["approved"] = approval
                    target = Path(root) / "blocked.pdf"
                    with self.assertRaisesRegex(MagazineValidationError, "approval is a separate gate"):
                        render_magazine(data, target)
                    self.assertFalse(target.exists())

    def test_requires_distinct_source_hosts(self):
        with tempfile.TemporaryDirectory() as root:
            data = mock_pack()
            data["pages"][3]["evidence"][1]["source_url"] = "https://www.nseindia.com/other"
            data["pages"][3]["evidence"][2]["source_url"] = "https://nseindia.com/third"
            with self.assertRaises(MagazineValidationError):
                render_magazine(data, Path(root) / "nope.pdf")

    def test_non_public_citation_hosts_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            for url in (
                "https://localhost/source",
                "https://127.0.0.1/source",
                "https://10.0.0.1/source",
                "https://example.invalid/preview",
                "https://internal.test/report",
                "https://singlelabel/report",
            ):
                with self.subTest(url=url):
                    data = mock_pack()
                    data["pages"][2]["evidence"][0]["source_url"] = url
                    target = Path(root) / "withheld.pdf"
                    with self.assertRaises(MagazineValidationError):
                        render_magazine(data, target)
                    self.assertFalse(target.exists())

    def test_invalid_sources_block_output(self):
        with tempfile.TemporaryDirectory() as root:
            data = mock_pack()
            data["pages"][2]["evidence"][0]["source_url"] = "http://insecure.example"
            with self.assertRaises(MagazineValidationError):
                render_magazine(data, Path(root) / "nope.pdf")

if __name__ == "__main__":
    unittest.main()
