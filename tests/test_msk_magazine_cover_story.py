"""Each monthly cover must reflect dated verified market events, not a festival.

No live network. Record-high and gold news may not be invented from returns.
"""
import copy
from pathlib import Path
import json
import unittest
from scripts.msk_magazine_cover_story import CoverStoryError, select_cover_story

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs/msk_magazine/ISSUE_2026-11_OFFICIAL_RESEARCH_METRICS.json"


class MonthlyCoverStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metric_pack = json.loads(PATH.read_text(encoding="utf-8"))

    def test_november_uses_actual_covered_month_numbers(self):
        cover = select_cover_story("2026-11", self.metric_pack)
        self.assertEqual(cover["headline"], "NIFTY RISES. SMALL CAPS FALL.")
        self.assertEqual(cover["kind"], "index_divergence")
        self.assertEqual(cover["source_date"], "2026-10-31")
        self.assertIn("Nifty 50 +1.95%", cover["deck"])
        self.assertIn("Smallcap 250 -5.78%", cover["deck"])
        self.assertNotIn("Diwali", cover["headline"])
        self.assertEqual(len(cover["evidence"]), 2)

    def test_generic_draft_does_not_invent_event(self):
        self.assertIsNone(select_cover_story("2026-12", None))
        stripped = copy.deepcopy(self.metric_pack)
        stripped.pop("cover_feature")
        self.assertIsNone(select_cover_story("2026-11", stripped))

    def test_wrong_issue_rejected(self):
        with self.assertRaisesRegex(CoverStoryError, "wrong month"):
            select_cover_story("2026-12", self.metric_pack)

    def test_other_supported_sector_topic_uses_its_real_data(self):
        pack = copy.deepcopy(self.metric_pack)
        pack["cover_feature"] = {
            "kind": "sector_movement",
            "sector": "Nifty IT",
            "observation_month": "2026-10",
        }
        cover = select_cover_story("2026-11", pack)
        self.assertIn("IT LEADS THE RALLY", cover["headline"])
        self.assertIn("+18.06%", cover["deck"])

    def test_unverified_gold_fall_or_all_time_high_rejected(self):
        for story in ("gold_drop", "bank_nifty_all_time_high", "market_record"):
            pack = copy.deepcopy(self.metric_pack)
            pack["cover_feature"]["kind"] = story
            with self.subTest(story=story):
                with self.assertRaisesRegex(CoverStoryError, "no verified data contract"):
                    select_cover_story("2026-11", pack)

    def test_diwali_slogan_cannot_be_injected_into_monthly_cover(self):
        pack = copy.deepcopy(self.metric_pack)
        pack["cover_feature"]["special_edition"] = True
        with self.assertRaisesRegex(CoverStoryError, "special issue"):
            select_cover_story("2026-11", pack)

    def test_false_divergence_rejected(self):
        pack = copy.deepcopy(self.metric_pack)
        cap = next(x for x in pack["market_indices"] if x["name"] == "Nifty Smallcap 250")
        cap["m1"] = 6.5
        with self.assertRaisesRegex(CoverStoryError, "not supported"):
            select_cover_story("2026-11", pack)

    def test_source_and_observation_must_be_verified(self):
        pack = copy.deepcopy(self.metric_pack)
        pack["cover_feature"]["observation_month"] = "2026-11"
        with self.assertRaisesRegex(CoverStoryError, "prior month"):
            select_cover_story("2026-11", pack)
        pack = copy.deepcopy(self.metric_pack)
        pack["market_source"]["url"] = "https://example.org/October-dashboard.pdf"
        with self.assertRaisesRegex(CoverStoryError, "Unsupported"):
            select_cover_story("2026-11", pack)

    def test_unrealistic_figure_rejected(self):
        pack = copy.deepcopy(self.metric_pack)
        cap = next(x for x in pack["market_indices"] if x["name"] == "Nifty Smallcap 250")
        cap["m1"] = float("nan")
        with self.assertRaisesRegex(CoverStoryError, "Non-finite"):
            select_cover_story("2026-11", pack)


if __name__ == "__main__":
    unittest.main()
