"""MSK index Top-5 regression — offline only, no real NSE/Telegram orders."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import index_constituents as ic
import index_top5 as top5

ROOT = Path(__file__).resolve().parents[1]


def nse_csv(n):
    return ("Company Name,Industry,Symbol,Series,ISIN Code\n"
            + "".join(f"Example {i},Sector{i%4},TEST{i:03d},EQ,IN{i:010d}\n"
                      for i in range(n))).encode("utf-8")


class OfficialIndexMembershipTests(unittest.TestCase):
    def test_nse_csv_accepts_correct_counts_and_preserves_industry(self):
        for profile, count in (("conservative", 100), ("moderate", 150), ("aggressive", 250)):
            symbols, industries = ic.parse_constituents(nse_csv(count), profile)
            self.assertEqual(len(symbols), count)
            self.assertEqual(symbols[0], "TEST000")
            self.assertEqual(industries["TEST001"], "Sector1")

    def test_rejects_bogus_html_wrong_count_and_duplicate_symbols(self):
        for profile in ic.INDEXES:
            with self.assertRaises(ValueError):
                ic.parse_constituents(b"<html>NSE access denied</html>", profile)
            with self.assertRaises(ValueError):
                ic.parse_constituents(nse_csv(20), profile)
        duplicated = b"Symbol,Industry,Series\n" + b"TEST,Tech,EQ\n" * 100
        with self.assertRaises(ValueError):
            ic.parse_constituents(duplicated, "conservative")

    def test_fetches_and_caches_official_current_membership(self):
        response = Mock()
        response.content = nse_csv(150)
        response.raise_for_status.return_value = None
        with patch.object(ic, "_CACHE", {}), patch.object(ic.requests, "get", return_value=response) as get:
            res = ic.get_index_universe("moderate")
            self.assertEqual(len(res["symbols"]), 150)
            self.assertEqual(res["name"], "Nifty Midcap 150")
            self.assertIn("nsearchives.nseindia.com", get.call_args.args[0])
            again = ic.get_index_universe("moderate")
            self.assertTrue(again["cached"])
            self.assertEqual(get.call_count, 1)

    def test_no_arbitrary_fallback_when_index_unavailable(self):
        with patch.object(ic, "_CACHE", {}), patch.object(ic.requests, "get", side_effect=ic.requests.RequestException):
            with self.assertRaises(RuntimeError):
                ic.get_index_universe("aggressive")


class Top5ScoringTests(unittest.TestCase):
    def test_rank_top_five_diversified_and_no_forced_picks(self):
        rows = [dict(sym=f"X{i}", score=100-i, sector="Tech" if i < 5 else "Bank",
                     volx=1.4) for i in range(10)]
        selected = top5._select_top_five(rows)
        self.assertEqual(len(selected), 4)  # Two Tech and two Bank, not forced.
        self.assertEqual([x["sym"] for x in selected], ["X0", "X1", "X5", "X6"])

    def test_low_coverage_withholds_rankings(self):
        universe = {"symbols": tuple(f"S{i}" for i in range(100)), "cap": "Large Cap",
                    "name": "Nifty 100", "source": "Official NSE constituent CSV",
                    "asof": "29 Oct 2026", "industries": {}}
        out = top5._format_index_result("conservative", universe, [], 20)
        self.assertIn("Insufficient index coverage", out)
        self.assertNotIn("1. S", out)
        self.assertIn("No securities recommended", out)

    def test_format_five_never_six_and_carries_index_name(self):
        universe = {"symbols": tuple(f"S{i}" for i in range(100)), "cap": "Large Cap",
                    "name": "Nifty 100", "source": "Official NSE constituent CSV",
                    "asof": "29 Oct 2026", "industries": {}}
        rows = [dict(sym=f"SYM{i}", sector=f"Industry{i%4}", score=100-i,
                     combined_score=90-i, fund_score=80-i, roe=16.5, pe=21.0,
                     de=.25, pb=None, fin=False, profit_growth=17.0,
                     sales_growth=12.0, quarter="2026-06-30",
                     ltp=150, chg=1.2, rsi=60, entry=150, sl=140, t1=170,
                     t2=185, rr1=1.8, asof="2026-10-28") for i in range(12)]
        out = top5._format_index_result("conservative", universe, rows, 97)
        self.assertIn("Nifty 100", out)
        self.assertIn("5/5 qualified", out)
        self.assertEqual(out.count("📘 ROE"), 5)
        self.assertEqual(out.count("Entry ref ₹"), 5)
        self.assertNotIn("SYM5", out)

    def test_full_index_data_not_fixed_20_stock_seed(self):
        histories = {"TEST000": Mock(empty=False)}
        universe = {"symbols": tuple(f"TEST{i:03d}" for i in range(250)),
                    "cap": "Small Cap", "name": "Nifty Smallcap 250",
                    "source": "Official NSE constituent CSV", "industries": {},
                    "asof": "29 Oct 2026"}
        with patch.object(top5, "get_index_universe", return_value=universe), patch(
                "market_data.bulk_history", return_value=histories) as bulk, patch.object(
                top5, "evaluate_candidate", return_value=None) as ev:
            out = top5._build_index_top5("aggressive")
        self.assertEqual(len(bulk.call_args.args[0]), 250)
        self.assertEqual(bulk.call_args.kwargs["chunk_size"], 50)
        self.assertIn("Insufficient index coverage", out)
        ev.assert_called_once()

    def test_legacy_and_new_menu_buttons_route_to_same_index_scans(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"🏦 Large Cap Top 5"', source)
        self.assertIn('"⚖️ Mid Cap Top 5"', source)
        self.assertIn('"🚀 Small Cap Top 5"', source)
        self.assertIn('"🏦 Conservative": "conservative"', source)
        self.assertIn('"⚖️ Moderate": "moderate"', source)
        self.assertIn('"🚀 Aggressive": "aggressive"', source)
        self.assertIn('request_index_top5(profile, m.chat.id, safe_send)', source)


class ResultDeliveryTests(unittest.TestCase):
    def test_one_background_job_sends_once_to_each_waiting_chat(self):
        started = threading.Event()
        release = threading.Event()
        deliveries = []
        def calculate(profile):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return "📊 synthetic cached result"
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(
                top5, "_POOL", pool), patch.object(top5, "_PENDING", {}), patch.object(
                top5, "_WAITERS", {}), patch.object(top5, "_RESULTS", {}), patch.object(
                top5, "_build_index_top5", side_effect=calculate):
            ack1 = top5.request_index_top5("moderate", 101, lambda cid, msg: deliveries.append((cid, msg)))
            self.assertTrue(started.wait(1))
            ack2 = top5.request_index_top5("moderate", 202, lambda cid, msg: deliveries.append((cid, msg)))
            self.assertIn("scanning full", ack1)
            self.assertIn("already running", ack2)
            release.set()
            pool.shutdown(wait=True)
            self.assertEqual(sorted(cid for cid, _ in deliveries), [101, 202])
            cached = top5.request_index_top5("moderate", 303, lambda *a: None)
            self.assertIn("Cached recent index scan", cached)


if __name__ == "__main__":
    unittest.main()
