"""NSE primary-source archive collection: offline deterministic tests only."""
from datetime import date, datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

import requests

from scripts.msk_magazine_nse import _decode_source, collect
from scripts.msk_magazine_monthly import generate, validate_evidence

HEADER = b"Index Name,Index Date,Open,High,Low,Closing,Points Change,Change(%),Volume\n"
DATA = HEADER + (
    b"Nifty 50,30-11-2026,25000,25200,24900,25100.25,100.25,0.4,0\n"
    b"Nifty Bank,30-11-2026,60000,61000,59000,60500.50,500.50,0.8,0\n"
    b"Nifty Midcap 150,30-11-2026,22000,22600,21900,22550.75,550.75,2.5,0\n"
    b"Nifty Smallcap 250,30-11-2026,17000,17500,16900,17300.20,300.20,1.7,0\n"
)


class FakeResponse:
    def __init__(self, url, body, bad=False):
        self.url = url
        self.content = body
        self.bad = bad

    def raise_for_status(self):
        if self.bad:
            raise requests.HTTPError("403 forbidden")


class FakeSession:
    def __init__(self, body=DATA, bad=False, redirect=None):
        self.body = body
        self.bad = bad
        self.redirect = redirect
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        self.kwargs = kwargs
        return FakeResponse(self.redirect or url, self.body, self.bad)


class NseCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_official_csv_contains_four_required_indices(self):
        rows, meta = _decode_source(DATA)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["value"], "25100.25")
        self.assertEqual(rows[0]["observed"], date(2026, 11, 30))
        self.assertEqual(meta["total_rows"], 4)

    def test_valid_official_archive_creates_source_pack(self):
        session = FakeSession()
        result = collect("2026-12", self.folder, session=session,
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertTrue(result["success"])
        self.assertEqual(result["observations"], 8)
        self.assertTrue((self.folder / "source_pack.json").exists())
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(session.kwargs["timeout"], 15)

    def test_collected_pack_passes_local_byte_consistency(self):
        collect("2026-12", self.folder, session=FakeSession(),
                now=date(2026, 12, 7), lookback_days=0)
        pack = json.loads((self.folder / "source_pack.json").read_text())
        points, failures = validate_evidence(pack, "2026-12", self.folder,
                                             date(2026, 12, 7))
        self.assertEqual(sum(len(v) for v in points.values()), 8, failures)
        self.assertFalse(failures)

    def test_collected_pack_renders_draft_without_publishing(self):
        collect("2026-12", self.folder, session=FakeSession(),
                now=date(2026, 12, 7), lookback_days=0)
        result = generate(
            "2026-12", self.folder / "out",
            source_pack=self.folder / "source_pack.json",
            source_root=self.folder,
            scheduled=True, now=datetime(2026, 12, 7, 2, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(result["source_consistency_rows"], 8, result["source_failures"])
        self.assertTrue(result["mechanical_pdf_checks"]["passed"])
        self.assertFalse(result["publication_allowed"])

    def test_network_403_blocks_figures_and_saves_failures(self):
        result = collect("2026-12", self.folder, session=FakeSession(bad=True),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])
        self.assertEqual(result["observations"], 0)
        self.assertFalse((self.folder / "source_pack.json").exists())
        self.assertTrue((self.folder / "source_fetch_failures.json").exists())

    def test_archive_redirect_to_unofficial_domain_rejected(self):
        result = collect("2026-12", self.folder,
                         session=FakeSession(redirect="https://example.com/evil.csv"),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])
        self.assertEqual(result["observations"], 0)

    def test_index_date_mismatch_rejected(self):
        wrong = DATA.replace(b"30-11-2026", b"27-11-2026")
        result = collect("2026-12", self.folder, session=FakeSession(body=wrong),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])

    def test_wrong_archive_on_official_host_rejected_even_with_matching_rows(self):
        wrong = "https://archives.nseindia.com/content/indices/ind_close_all_27112026.csv"
        result = collect("2026-12", self.folder, session=FakeSession(redirect=wrong),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])
        self.assertFalse((self.folder / "source_pack.json").exists())

    def test_official_mirror_recorded_as_source_of_received_bytes(self):
        mirror = "https://nsearchives.nseindia.com/content/indices/ind_close_all_30112026.csv"
        result = collect("2026-12", self.folder, session=FakeSession(redirect=mirror),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertTrue(result["success"])
        pack = json.loads((self.folder / "source_pack.json").read_text())
        self.assertEqual({row["source_url"] for row in pack["observations"]}, {mirror})

    def test_duplicate_names_rejected(self):
        bad = DATA + DATA.splitlines()[1] + b"\n"
        result = collect("2026-12", self.folder, session=FakeSession(body=bad),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])

    def test_missing_index_rejected(self):
        bad = b"\n".join(DATA.splitlines()[:-1]) + b"\n"
        result = collect("2026-12", self.folder, session=FakeSession(body=bad),
                         now=date(2026, 12, 7), lookback_days=0)
        self.assertFalse(result["success"])

    def test_csv_missing_columns_rejected(self):
        with self.assertRaises(ValueError):
            _decode_source(b"name,close\nNifty 50,30000\n")

    def test_future_issue_rejected(self):
        with self.assertRaises(ValueError):
            collect("2027-01", self.folder, now=date(2026, 12, 7))


if __name__ == "__main__":
    unittest.main()
