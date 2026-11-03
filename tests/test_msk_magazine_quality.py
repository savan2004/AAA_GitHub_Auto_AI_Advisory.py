"""Magazine editorial acceptance tests (offline)."""
import unittest
from msk_magazine_quality import audit_release_pack


class MagazineQualityTests(unittest.TestCase):
    def test_rejects_empty_and_layout_only(self):
        self.assertTrue(audit_release_pack(None))
        self.assertTrue(audit_release_pack({"pages": [{} for _ in range(16)]}))

    def test_rejects_repeated_filler(self):
        pack = {"approved": True, "approved_by": "Editor", "as_of": "2026-10-30",
                "pages": [{} for _ in range(16)]}
        for i in range(2, 15):
            pack["pages"][i] = {"summary": "RESEARCH CONTENT PENDING SOURCE VALIDATION. " * 3,
                                "evidence": []}
        result = audit_release_pack(pack)
        self.assertTrue(any("placeholder" in x for x in result))
        self.assertTrue(any("Repeated" in x for x in result))

    def test_rejects_synthetic_claims_even_with_sources(self):
        facts = [{"statement": "Synthetic fixture", "source_url": "https://www.nseindia.com/a",
                  "observed_on": "2026-10-30"},
                 {"statement": "Synthetic fixture", "source_url": "https://www.rbi.org.in/b",
                  "observed_on": "2026-10-30"},
                 {"statement": "Synthetic fixture", "source_url": "https://www.sebi.gov.in/c",
                  "observed_on": "2026-10-30"}]
        pack = {"approved": True, "approved_by": "Editor", "as_of": "2026-10-30",
                "pages": [{} for _ in range(16)]}
        for i in range(2, 15):
            pack["pages"][i] = {"summary": f"Distinct page {i}: " + "market commentary " * 12,
                                "evidence": facts}
        self.assertTrue(any("synthetic" in x for x in audit_release_pack(pack)))


if __name__ == "__main__":
    unittest.main()
