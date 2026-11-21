import json
import unittest
from datetime import date, timedelta
from decimal import Decimal

from msk_ai_builder_automatic import approved_plan, ResearchUnavailable
from msk_ai_builder_profile import ResearchProfile


SESSION = date(2026, 11, 20)


def sample():
    rows = []
    for i, (ticker, sector, score) in enumerate((("SBIN", "Banking", 90),
                                                 ("INFY", "IT", 89),
                                                 ("TCS", "IT", 85))):
        rows.append(dict(symbol=ticker, isin=f"IN{i:010d}", sector=sector,
                         close_date=SESSION.isoformat(), close_rupees="900", score=score,
                         evidence_status="PRIMARY_RECONCILED", price_status="EXCHANGE_VERIFIED",
                         filing_url="https://nsearchives.nseindia.com/test-filing",
                         price_source_url="https://nsearchives.nseindia.com/test-close"))
    return dict(schema_version=1, publication_status="APPROVED",
                close_date=SESSION.isoformat(), companies=rows)


class AutomaticResearchTests(unittest.TestCase):
    def test_approved_input_automatically_selects_and_sizes(self):
        result = approved_plan(ResearchProfile(15000000, "medium", 36),
                               json.dumps(sample()), latest_session=SESSION)
        self.assertEqual([row.candidate.symbol for row in result.positions], ["SBIN", "INFY"])
        self.assertTrue(all(row.shares == 2500 for row in result.positions))
        self.assertEqual(result.cash, Decimal(10500000))

    def test_withheld_missing_stale_mixed_and_unverified_never_produce_picks(self):
        withheld, mixed, secondary = sample(), sample(), sample()
        withheld["publication_status"] = "WITHHELD"
        mixed["companies"][0]["close_date"] = (SESSION - timedelta(days=1)).isoformat()
        secondary["companies"][0]["evidence_status"] = "SECONDARY_ONLY"
        profile = ResearchProfile(15000000, "medium", 36)
        for data, latest in ((None, SESSION), (withheld, SESSION), (mixed, SESSION),
                             (secondary, SESSION), (sample(), SESSION + timedelta(days=8))):
            with self.assertRaises(ResearchUnavailable):
                approved_plan(profile, json.dumps(data), latest_session=latest)
