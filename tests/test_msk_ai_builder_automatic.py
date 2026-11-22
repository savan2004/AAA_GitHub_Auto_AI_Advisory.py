import json
import unittest
from datetime import date, timedelta
from decimal import Decimal

from msk_ai_builder_automatic import approved_plan, ResearchUnavailable, read_approved_artifact
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


class ApprovedArtifactSourceTests(unittest.TestCase):
    def test_exact_digest_file_and_large_history(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from hashlib import sha256
        from test_msk_ai_builder_optimizer import fixture
        _,history=fixture()
        import numpy as np
        import pandas as pd
        rng=np.random.default_rng(911)
        days=pd.bdate_range(end=SESSION,periods=252)
        payload=sample()
        template=payload["companies"][0]
        payload["schema_version"]=2
        payload["companies"]=[dict(template,symbol=f"TEST{i:02d}",isin=f"IN{i:010d}",
                                  sector=f"Sector {i}",close_rupees="100",score=95-i)
                              for i in range(20)]
        payload["risk_history"]=dict(status="APPROVED_ADJUSTED_DAILY",
            sessions=[d.date().isoformat() for d in days],
            returns_by_symbol={row["symbol"]:rng.normal(0,.01,len(days)).tolist()
                               for row in payload["companies"]})
        raw=json.dumps(payload).encode()
        self.assertGreater(len(raw),64000)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"approved.json"
            path.write_bytes(raw)
            env=dict(MSK_BUILDER_V2_APPROVED_RESEARCH_FILE=str(path),
                     MSK_BUILDER_V2_APPROVED_RESEARCH_SHA256=sha256(raw).hexdigest())
            loaded=read_approved_artifact(env)
            plan=approved_plan(ResearchProfile(15000000,"medium",36),loaded,
                               latest_session=SESSION,optimize=True)
            self.assertEqual(len(plan.positions),20)
            self.assertEqual(plan.cash+sum(p.cost for p in plan.positions),15000000)
            for bad in (dict(env,MSK_BUILDER_V2_APPROVED_RESEARCH="{}"),
                        dict(env,MSK_BUILDER_V2_APPROVED_RESEARCH_SHA256="0"*64),
                        dict(env,MSK_BUILDER_V2_APPROVED_RESEARCH_FILE="relative.json")):
                with self.assertRaises(ResearchUnavailable):
                    read_approved_artifact(bad)
            path.write_bytes(raw+b" ")
            with self.assertRaises(ResearchUnavailable):
                read_approved_artifact(env)

    def test_env_source_stays_bounded(self):
        self.assertEqual(read_approved_artifact({"MSK_BUILDER_V2_APPROVED_RESEARCH":"{}"}),"{}")
        for env in ({},{"MSK_BUILDER_V2_APPROVED_RESEARCH":"x"*64001},
                    {"MSK_BUILDER_V2_APPROVED_RESEARCH_FILE":"/not-present"},
                    {"MSK_BUILDER_V2_APPROVED_RESEARCH_SHA256":"0"*64}):
            with self.assertRaises(ResearchUnavailable):
                read_approved_artifact(env)
