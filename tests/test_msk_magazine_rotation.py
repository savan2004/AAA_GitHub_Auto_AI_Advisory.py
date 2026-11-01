"""Strict offline tests for draft-only, no-repeat MSK magazine topic planner."""
import unittest
from datetime import date

from msk_magazine_rotation import (
    TOPICS, ANGLES, draft_article, first_monday, plan_volume,
)


def month_at(index):
    year = 2026 + index // 12
    month = index % 12 + 1
    return f"{year:04d}-{month:02d}"


class MagazineRotationTests(unittest.TestCase):
    def test_first_monday_ist_calendar_date(self):
        self.assertEqual(first_monday(2026, 11), date(2026, 11, 2))
        self.assertEqual(first_monday(2026, 12), date(2026, 12, 7))

    def test_100_editions_unique_and_auto_refresh_at_16(self):
        ledger = []
        styles = []
        for i in range(100):
            draft = plan_volume(month_at(i), ledger)
            self.assertNotIn(draft["article_key"], {row["article_key"] for row in ledger})
            self.assertIs(draft["approved"], False)
            self.assertEqual(draft["volume"], i + 1)
            styles.append(draft["format"])
            if i == 15:
                self.assertTrue(draft["style_refreshed"])
                self.assertNotEqual(styles[14], styles[15])
            if i == 14:
                self.assertFalse(draft["style_refreshed"])
            ledger.append({"issue": draft["issue"], "article_key": draft["article_key"]})
        self.assertEqual(len({row["article_key"] for row in ledger}), 100)
        self.assertEqual(len(TOPICS) * len(ANGLES), 256)

    def test_12_volume_refresh_can_be_selected(self):
        ledger = []
        for i in range(13):
            item = plan_volume(month_at(i), ledger, refresh_every=12)
            if i == 12:
                self.assertTrue(item["style_refreshed"])
                self.assertEqual(item["editorial_cycle"], 2)
            ledger.append({"issue": item["issue"], "article_key": item["article_key"]})

    def test_missing_or_corrupt_ledger_fails_closed(self):
        with self.assertRaises(ValueError):
            plan_volume("2026-11", None)
        with self.assertRaises(ValueError):
            plan_volume("2026-13", [])
        with self.assertRaises(ValueError):
            plan_volume("2026-11", [{"issue": "2026-11", "article_key": "x/0"}])
        with self.assertRaises(ValueError):
            plan_volume("2026-12", [{"issue": "2026-11", "article_key": "x/0"},
                                    {"issue": "2026-11", "article_key": "x/1"}])
        with self.assertRaises(ValueError):
            plan_volume("2026-11", [], refresh_every=0)

    def test_runout_never_cycles_identical_article_key(self):
        # Synthetic 256 published key combinations; no silent reset.
        ledger = []
        for i, (slug, _, _) in enumerate(TOPICS):
            for j in range(len(ANGLES)):
                issue = month_at(i * len(ANGLES) + j)
                ledger.append({"issue": issue, "article_key": f"{slug}/{j}"})
        with self.assertRaisesRegex(RuntimeError, "exhausted"):
            plan_volume(month_at(len(ledger)), ledger)

    def test_draft_requires_sources_and_cannot_be_published(self):
        plan = plan_volume("2026-11", [])
        facts = [
            {"claim": "This is a dated market participation observation from NSE.",
             "url": "https://www.nseindia.com/reports", "as_of": "2026-10-30"},
            {"claim": "This is an RBI policy observation for the historical period.",
             "url": "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
             "as_of": "2026-10-30"},
            {"claim": "This is the historical oil price reference in EIA data.",
             "url": "https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm",
             "as_of": "2026-10-30"},
        ]
        result = draft_article(plan, facts, [])
        self.assertFalse(result["approved"])
        self.assertIn("UNAPPROVED", result["body"])
        self.assertEqual(len(result["content_sha256"]), 64)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            draft_article(plan, facts, [{
                "issue": "2026-10", "article_key": "sample/1",
                "content_sha256": result["content_sha256"],
                "article_body": result["body"]
            }])
        with self.assertRaisesRegex(ValueError, "source"):
            draft_article(plan, facts[:2], [])
        with self.assertRaises(ValueError):
            draft_article(plan, facts[:2] + [{**facts[2], "url": "http://test.org"}], [])
        with self.assertRaises(ValueError):
            draft_article(plan, facts[:2] + [{**facts[2], "as_of": "2026-11-10"}], [])


if __name__ == "__main__":
    unittest.main()
