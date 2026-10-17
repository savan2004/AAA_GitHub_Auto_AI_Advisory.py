"""Offline tests (no network, no Telegram). Run:  python -m unittest discover -s tests -v"""
import os
import sys
import copy
import ast
import hashlib
import pathlib
import inspect
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test:token")
os.environ.setdefault("WEBHOOK_URL", "https://example.invalid")
os.environ.setdefault("MPLBACKEND", "Agg")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fixtures as fx                      # noqa: E402
import fundamental_pipeline as fp          # noqa: E402
import research_engine as eng              # noqa: E402
import report_generator as rg              # noqa: E402
import report_charts as charts             # noqa: E402

IST = fp.IST
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=IST)
CH = "-1004429986085"
GOOD = {"mcap": 52000e7, "pe": 24.0, "pb": 5.0, "roe": 21.0, "roce": 26.0, "de": 0.03, "ocf_pat": 1.1, "rev_cagr3": 15.6,
        "pat_cagr3": 16.5, "down_years": 0, "book_growth": None, "lev": None, "lender": False, "lender_kind": None,
        "fin": False, "n_years": 6, "fresh": "FRESH"}


def pdf_text(path):
    out = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout
    return " ".join(out.split())


class FakeDeps:
    def __init__(self, symbols, inputs, metrics=None, build=None, delay=0.0):
        self.symbols, self.inputs, self.metrics = symbols, inputs, metrics or {s: GOOD for s in symbols}
        self.build, self.delay, self.collected = build, delay, []

    def universe(self, now):
        return {"symbols": self.symbols, "isin": {}}

    def fetch_compact(self, sym):
        return self.metrics.get(sym)

    def collect(self, sym):
        self.collected.append(sym)
        time.sleep(self.delay)
        return copy.deepcopy(self.inputs[sym])

    def assemble(self, inp):
        return rg.assemble(inp, use_ai=False)

    def build_pdf(self, R, path):
        return self.build(R, path) if self.build else rg.build_pdf(R, path)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        fp.SCREEN_FILE = os.path.join(self.tmp, "screen.json")
        fp.UNIVERSE_FILE = os.path.join(self.tmp, "uni.json")
        fp.ISIN_MAP_FILE = os.path.join(self.tmp, "isin.json")
        self.ledger = fp.Ledger(os.path.join(self.tmp, "ledger.db"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_prepare(self, deps, ledger=None):
        return fp.prepare(CH, NOW, deps, ledger or self.ledger, out_dir=self.tmp)


# 1. valid candidate -> matching PDF + caption
class T01_ValidCandidate(Base):
    def test_matching_pdf_and_caption(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()})
        res = self.run_prepare(deps)
        self.assertEqual(res["status"], "READY", res)
        self.assertEqual(res["symbol"], "STRONGCO")
        self.assertTrue(os.path.getsize(res["pdf"]) > 15000)
        text = pdf_text(res["pdf"])
        self.assertIn("STRONGCO", text)
        self.assertIn("Strong Compounders Limited", text)
        cap = res["caption"]
        self.assertTrue(cap.startswith("Strong Compounders Limited | STRONGCO"))
        for frag in ("Fundamental Research | 16-Oct-2026", "Stance:", "Fundamental assessment:", "Technical timing:",
                     "Why selected:", "Main risk:", "Full research PDF attached.", "no guaranteed returns"):
            self.assertIn(frag, cap)
        self.assertLessEqual(len(cap), fp.CAPTION_LIMIT)
        self.assertIn(res["report_id"], os.path.basename(res["pdf"]))
        self.assertEqual(deps.collected, ["STRONGCO"])                      # one deep refresh, not a full scan


# 2. similar symbols -> no substitution
class T02_SimilarSymbols(Base):
    def test_canonical_dedupe_and_equity_only(self):
        syms, st = fp.clean_universe(["TRIDENT", "TRIDENT.NS", "TRIDENT.BO", "NIFTYBEES", "GOLDBEES", "GOLDIAM", "ABC-RE",
                                      "BAJAJ-AUTO", "TRIDENTLTD", "LIQUIDBEES"])
        self.assertEqual(syms, ["TRIDENT", "GOLDIAM", "BAJAJ-AUTO", "TRIDENTLTD"])
        self.assertEqual(st["duplicates_removed"], 2)
        syms2, _ = fp.clean_universe(["AAA", "BBB"], {"AAA": "INE001", "BBB": "INE001"})     # same ISIN = same company
        self.assertEqual(syms2, ["AAA"])

    def test_provider_substitution_is_blocked(self):
        wrong = fx.strong(sym="TRIDENTLTD", name="Trident Tech Limited")        # provider answers with a similar symbol
        good = fx.strong(sym="TRIDENTX", name="Trident X Limited")
        deps = FakeDeps(["TRIDENT", "TRIDENTX"], {"TRIDENT": wrong, "TRIDENTX": good})
        res = self.run_prepare(deps)
        self.assertEqual(res["status"], "READY", res)
        self.assertEqual(res["symbol"], "TRIDENTX")
        self.assertTrue(any(s.startswith("TRIDENT:") and "symbol" in s for s in res["skipped_candidates"]), res["skipped_candidates"])


# 3. missing optional peers -> transparent, nothing invented
class T03_MissingPeers(Base):
    def test_no_invented_peer_values(self):
        R = rg.assemble(fx.strong(), use_ai=False)
        self.assertIsNone(R["fv"]["peer_pe"])
        self.assertIsNone(R["fv"]["implied_peer_value"])
        self.assertIn("Peer-median P/E", R["completeness"]["missing_optional"])
        self.assertEqual(R["completeness"]["missing_mandatory"], [])
        p = os.path.join(self.tmp, "a.pdf")
        rg.build_pdf(R, p)
        text = pdf_text(p)
        self.assertIn("Optional data not available", text)
        self.assertIn("no peer group could be resolved", text)
        self.assertNotIn("Peer-multiple cross-check", text)
        self.assertNotIn("Street consensus (Yahoo)", text)


# 4. missing mandatory -> replacement or safe skip
class T04_MissingMandatory(Base):
    def test_screen_never_treats_missing_as_zero(self):
        r = fp.screen_metrics({**GOOD, "roe": None})
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing_mandatory"], ["roe"])
        self.assertEqual(r["fails"], [])                                   # missing is not a "fail"

    def test_sector_appropriate_and_optional_fields(self):
        lender = {"mcap": 90000e7, "pe": 14.0, "pb": 2.4, "roe": 15.0, "roce": None, "de": None, "ocf_pat": None, "rev_cagr3": 15,
                  "pat_cagr3": 18, "down_years": 0, "book_growth": 20.0, "lev": 6.0, "lender": True, "lender_kind": "NBFC",
                  "fin": True, "n_years": 6, "fresh": "FRESH"}
        self.assertTrue(fp.screen_metrics(lender)["ok"])                   # no OCF / D/E rules for lenders
        self.assertTrue(fp.screen_metrics(GOOD)["ok"])                     # no forward P/E / peers needed
        self.assertFalse(fp.screen_metrics({**GOOD, "ocf_pat": 0.3})["ok"])
        self.assertFalse(fp.screen_metrics({**GOOD, "fresh": "STALE"})["ok"])

    def test_replacement_candidate_then_safe_skip(self):
        bad_fund = fx.strong(sym="AAA", name="Aaa Limited")
        bad_fund["fund"] = {**bad_fund["fund"], "pe": None}                # cached screen said OK, fresh data lacks P/E
        good = fx.strong(sym="BBB", name="Bbb Limited")
        res = self.run_prepare(FakeDeps(["AAA", "BBB"], {"AAA": bad_fund, "BBB": good}, {"AAA": {**GOOD, "roe": 30.0}, "BBB": GOOD}))
        self.assertEqual(res["status"], "READY", res)
        self.assertEqual(res["symbol"], "BBB")
        self.assertTrue(any("mandatory data missing" in s for s in res["skipped_candidates"]))
        self.setUp()
        res2 = self.run_prepare(FakeDeps(["AAA"], {"AAA": bad_fund}))
        self.assertEqual(res2["status"], "SKIP")
        self.assertTrue(res2["alert"])
        self.assertEqual(self.ledger.get(fp.Ledger.day_key(CH, "2026-10-16"))["state"], "skipped")


# 5. stale data -> block / warn per policy
class T05_Stale(Base):
    def _gate(self, inp):
        R = rg.assemble(inp, use_ai=False)
        p = os.path.join(self.tmp, "s.pdf")
        rg.build_pdf(R, p)
        return fp.validate_report({"symbol": inp["sym"]}, inp, R, p, fp.build_caption(R, "x"), NOW)

    def test_old_prices_block(self):
        inp = fx.strong()
        inp["df"] = inp["df"].iloc[:-8]                                    # last candle ~10 days old
        g = self._gate(inp)
        self.assertTrue(any("price data is" in e for e in g.errors), g.errors)

    def test_stale_quarters_block_and_lagging_warns(self):
        inp = fx.strong()
        inp["stm"]["freshness"] = {**inp["stm"]["freshness"], "status": "STALE", "label": "Dec 2025"}
        self.assertTrue(any("stale" in e for e in self._gate(inp).errors))
        inp = fx.strong()
        inp["stm"]["freshness"] = {**inp["stm"]["freshness"], "status": "LAGGING"}
        g = self._gate(inp)
        self.assertFalse(any("stale" in e for e in g.errors))
        self.assertTrue(any("one cycle behind" in w for w in g.warnings))


# 6. annual vs quarterly metrics: labels, values, CAGR, EPS
class T06_AnnualVsQuarterly(Base):
    def setUp(self):
        super().setUp()
        self.R = rg.assemble(fx.trident(), use_ai=False)
        self.pdf = os.path.join(self.tmp, "t.pdf")
        rg.build_pdf(self.R, self.pdf)
        self.text = pdf_text(self.pdf)

    def test_margins_labelled_and_correct(self):
        fv = self.R["fv"]
        self.assertEqual((fv["opm"], fv["opm_label"]), (13.0, "Mar 2026"))
        self.assertEqual((fv["opm_q"], fv["opm_q_label"]), (17.0, "Jun 2026"))
        self.assertIn("FY (Mar 2026)", self.text)
        self.assertIn("Latest-quarter margin (Jun 2026)", self.text)

    def test_cagr_matches_annual_table(self):
        fv = self.R["fv"]
        self.assertEqual(fv["rev_cagr3"], round(((6701 / 6332) ** (1 / 3) - 1) * 100, 1))   # 1.9, not vendor's 2.0
        self.assertEqual(fv["pat_cagr3"], round(((377 / 442) ** (1 / 3) - 1) * 100, 1))      # -5.2, not vendor's -6.0
        self.assertEqual(fv["pat_cagr5"], round(((377 / 304) ** (1 / 5) - 1) * 100, 1))
        self.assertIn("annual table Mar 2023 to Mar 2026", fv["cagr_basis"]["rev_cagr3"])
        rows = [{"label": "a", "net_profit": 0}, {"label": "b", "net_profit": 5}, {"label": "c", "net_profit": 6}, {"label": "d", "net_profit": 7}]
        v, note, *_ = eng._cagr_checked(rows, "net_profit", 3)
        self.assertIsNone(v)
        self.assertIn("zero or negative", note)                                              # explicit, not silently 0
        v2, _, *_ = eng._cagr_checked([{"label": "a", "net_profit": 5}] * 3 + [{"label": "d", "net_profit": -2}], "net_profit", 3)
        self.assertIsNone(v2)

    def test_eps_ttm_derived_and_labelled(self):
        fv = self.R["fv"]
        self.assertEqual((fv["eps"], fv["eps_src"]), (0.78, "derived"))                      # 0.18+0.09+0.20+0.31
        self.assertIn("EPS (TTM, derived)", self.text)
        self.assertIn("derived: sum of last 4 quarterly EPS (Sep 2025 to Jun 2026)", self.text)
        q = copy.deepcopy(fx.trident()["stm"])
        q["quarters"][3]["label"] = "Jan 2026"                                               # non-contiguous periods -> refuse
        self.assertIsNone(eng._eps_ttm_from_quarters(q["quarters"], q["years"], 28.7, 22.27, "FRESH"))
        q2 = copy.deepcopy(fx.trident()["stm"])
        self.assertIsNone(eng._eps_ttm_from_quarters(q2["quarters"], q2["years"], 28.7, 220.0, "FRESH"))   # disagrees with price/PE

    def test_coverage_not_conflated(self):
        self.assertIn("Pillar coverage 100% (6/6)", self.text)
        self.assertIn("Mandatory data 4/4", self.text)
        self.assertNotIn("Data coverage 100%", self.text)
        self.assertIn("Field completeness", self.text)
        self.assertNotIn("core fields available", self.text)

    def test_sector_unknown_not_guessed(self):
        R = rg.assemble(fx.trident(sector="Others"), use_ai=False)
        self.assertEqual(R["sector"], "")
        p = os.path.join(self.tmp, "o.pdf")
        rg.build_pdf(R, p)
        self.assertIn("Sector unavailable", pdf_text(p))
        self.assertNotIn("Others", pdf_text(p))

    def test_price_timestamp_not_called_live(self):
        self.assertIn("close of last completed session (16-Oct-2026), not live", self.text)

    def test_news_filter_and_omission(self):
        junk = ["Trident Ltd Share Price Today, 22.26", "Trident Share Price Today, Trident Stock Price Live NSE/BSE Updates",
                "Trident Ltd - Share/Stock Price", "Trident Share Price Today 16 Oct 2026: Live NSE/BSE Rates, Technical Analysis and Expert"]
        self.assertTrue(all(rg._QUOTE_PAGE_RE.search(j) for j in junk))
        self.assertNotIn("Recent headlines", self.text)                                      # no dated, sourced news -> omitted


# 7. score arithmetic + narrative/plan consistency
class T07_ScoresAndNarrative(Base):
    def setUp(self):
        super().setUp()
        self.inp = fx.trident()
        self.R = rg.assemble(self.inp, use_ai=False)
        self.pdf = os.path.join(self.tmp, "t.pdf")
        rg.build_pdf(self.R, self.pdf)

    def test_composite_arithmetic(self):
        sc = self.R["sc"]
        raw = sum(eng.WEIGHTS[k] * v for k, v in sc["pillars"].items()) / sum(eng.WEIGHTS.values())
        self.assertAlmostEqual(raw, sc["composite_raw"], delta=0.6)
        self.assertEqual(sc["composite"], int(sc["composite_raw"] + 0.5))
        self.assertEqual(sc["stance"], fp._expected_stance(sc["composite"], sc["pillars"]["Trend"]))
        self.assertIn(str(sc["composite"]), self.R["nar"]["summary"])
        self.assertIn("Composite arithmetic", pdf_text(self.pdf))

    def test_missing_pillar_reweighting_explicit(self):
        fv = {k: (None if k in ("roe", "roce", "pe", "pb", "peg", "pe_prem", "rev_cagr3", "pat_cagr3", "q_rev_yoy", "q_pat_yoy",
                                "opm_delta", "ocf_pat", "de", "down_years") else v) for k, v in self.R["fv"].items()}
        fv["has_statements"] = False
        s2 = eng.compute_scores(self.R["t"], self.R["rk"], fv)
        self.assertTrue(s2["missing_pillars"], s2)
        self.assertLess(s2["pillar_coverage"], 100)
        self.assertAlmostEqual(sum(s2["weights_eff"].values()), 100.0, delta=0.3)

    def test_defensive_plan_levels_consistent(self):
        plan, t = self.R["plan"], self.R["t"]
        self.assertEqual(plan["mode"], "DEFENSIVE")
        self.assertLessEqual(plan["early_trigger"], plan["confirm_level"])
        self.assertLess(plan["bear_trigger"], t["ltp"])
        self.assertGreater(plan["early_trigger"], t["ltp"])
        out = self.R["nar"]["outlook"]
        self.assertIn("early recovery signal", out)
        self.assertNotIn("confirm upside continuation", out)
        text = pdf_text(self.pdf)
        self.assertIn("Early recovery", text)
        self.assertIn("Confirmed re-entry", text)
        self.assertIn(f"{plan['confirm_level']:,.2f}", text)
        self.assertNotIn("Target 1", text)                                                   # no forced targets in a defensive view
        self.assertIn("cost-of-equity hurdle assumed", text)                                 # ROE hurdle disclosed

    def test_adx_vs_direction_wording(self):
        tech = self.R["nar"]["technical"]
        self.assertIn("measures trend strength, not direction", tech)
        self.assertIn("By EMA alignment", tech)

    def test_gate_catches_inconsistency(self):
        pol = {"min_fund_avg": 0}
        base = lambda R: fp.validate_report({"symbol": "TRIDENT"}, self.inp, R, self.pdf, None, NOW, pol)
        self.assertTrue(base(self.R).ok, base(self.R).errors)
        R2 = copy.deepcopy(self.R)
        R2["sc"]["composite"] = 99
        self.assertTrue(any("composite" in e for e in base(R2).errors))
        R3 = copy.deepcopy(self.R)
        R3["plan"]["mode"] = "ACTIVE"
        self.assertTrue(any("contradict" in e for e in base(R3).errors))
        R4 = copy.deepcopy(self.R)
        R4["nar"]["outlook"] += " A close above would confirm upside continuation."
        self.assertTrue(any("confirmed upside" in e for e in base(R4).errors))
        g = fp.validate_report({"symbol": "TRIDENT"}, self.inp, self.R, self.pdf, None, NOW)   # default policy
        self.assertTrue(any("fundamental strength" in e for e in g.errors))                    # weak business is not published

    def test_chart_price_labels(self):
        self.assertEqual(charts._px(22.02), "₹22.02")
        self.assertEqual(charts._px(2450.4), "₹2,450")
        lv = charts._level_labels([("S", 22.02), ("S", 22.04), ("R", 22.44)], 0.5)
        ys = [y for _, _, y in lv]
        self.assertTrue(all(b - a >= 0.3 - 1e-9 for a, b in zip(ys, ys[1:])))
        self.assertEqual(sorted(p for _, p, _ in lv), [22.02, 22.04, 22.44])                # true levels untouched


# 8. duplicates / concurrency
class T08_Duplicates(Base):
    def test_concurrent_day_claim_single_winner(self):
        results = []
        ts = [threading.Thread(target=lambda: results.append(self.ledger.acquire_day(CH, "2026-10-16")[0])) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count("acquired"), 1, results)
        self.assertEqual(results.count("in_progress"), 9)

    def test_two_concurrent_jobs_one_publication(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()}, delay=0.4)
        out = []
        ts = [threading.Thread(target=lambda: out.append(self.run_prepare(deps))) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(o["status"] for o in out), ["IN_PROGRESS", "READY"], out)
        self.assertEqual(len(deps.collected), 1)                                            # only one deep refresh happened

    def test_after_sent_no_second_post_or_scan(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()})
        res = self.run_prepare(deps)
        fp.begin_send(CH, res["day"], res["symbol"], res["trade_date"], self.ledger)
        fp.confirm_sent(CH, res["day"], res["symbol"], res["trade_date"], 4242, self.ledger)
        again = self.run_prepare(deps)
        self.assertEqual(again["status"], "DUPLICATE")
        self.assertEqual(len(deps.collected), 1)
        row = self.ledger.get(fp.Ledger.co_key(CH, "STRONGCO", res["trade_date"]))
        self.assertEqual((row["state"], row["message_id"]), ("sent", "4242"))                # message id recorded after delivery

    def test_same_company_not_reposted_within_cooldown(self):
        d1 = FakeDeps(["AAA", "BBB"], {"AAA": fx.strong("AAA", "Aaa Limited"), "BBB": fx.strong("BBB", "Bbb Limited")},
                      {"AAA": {**GOOD, "roe": 30.0}, "BBB": GOOD})
        r1 = self.run_prepare(d1)
        fp.confirm_sent(CH, r1["day"], r1["symbol"], r1["trade_date"], 1, self.ledger)
        self.assertEqual(r1["symbol"], "AAA")
        nxt = fp.prepare(CH, datetime(2026, 10, 17, 12, 0, tzinfo=IST), d1, self.ledger, out_dir=self.tmp)
        self.assertEqual(nxt["status"], "READY")
        self.assertEqual(nxt["symbol"], "BBB")                                              # AAA is in cooldown


# 9. PDF / Telegram failure -> no false success
class T09_Failures(Base):
    def test_pdf_build_exception(self):
        def boom(R, p):
            raise RuntimeError("render failed")
        res = self.run_prepare(FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()}, build=boom))
        self.assertEqual(res["status"], "SKIP")
        self.assertNotEqual(self.ledger.get(fp.Ledger.day_key(CH, "2026-10-16"))["state"], "sent")

    def test_truncated_or_empty_pdf_never_ready(self):
        for payload in (b"", b"%PDF-1.4\n%truncated"):
            self.setUp()
            res = self.run_prepare(FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()},
                                            build=lambda R, p, b=payload: pathlib.Path(p).write_bytes(b)))
            self.assertEqual(res["status"], "SKIP", res)
            self.assertIn("PDF", res["reason"])

    def test_telegram_failure_states_and_retry_bound(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()})
        r = self.run_prepare(deps)
        fp.begin_send(CH, r["day"], r["symbol"], r["trade_date"], self.ledger)
        self.assertEqual(fp.record_failure(CH, r["day"], r["symbol"], r["trade_date"], "HTTP 400", False, self.ledger), "failed")
        r2 = self.run_prepare(deps)                                                          # attempt 2 allowed (definite failure)
        self.assertEqual(r2["status"], "READY")
        fp.record_failure(CH, r2["day"], r2["symbol"], r2["trade_date"], "HTTP 502", False, self.ledger)
        r3 = self.run_prepare(deps)                                                          # bound reached -> no third attempt
        self.assertEqual(r3["status"], "BLOCKED")
        self.assertTrue(r3["alert"])

    def test_ambiguous_timeout_is_never_retried(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()})
        r = self.run_prepare(deps)
        fp.begin_send(CH, r["day"], r["symbol"], r["trade_date"], self.ledger)
        fp.record_failure(CH, r["day"], r["symbol"], r["trade_date"], "timeout after request", True, self.ledger)
        again = self.run_prepare(deps)
        self.assertEqual((again["status"], again["alert"]), ("BLOCKED", True))
        self.assertEqual(len(deps.collected), 1)

    def test_crash_while_sending_treated_as_ambiguous(self):
        deps = FakeDeps(["STRONGCO"], {"STRONGCO": fx.strong()})
        r = self.run_prepare(deps)
        fp.begin_send(CH, r["day"], r["symbol"], r["trade_date"], self.ledger)               # process dies here
        self.assertEqual(self.run_prepare(deps)["status"], "BLOCKED")

    def test_universe_snapshot_reused_without_refetch(self):
        calls = []
        fetch = lambda: calls.append(1) or {"symbols": ["AAA", "BBB", "AAA.NS"], "source": "test", "degraded": False}
        u1 = fp.load_universe(NOW, fetch)
        u2 = fp.load_universe(NOW, fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(u1["symbols"], ["AAA", "BBB"])
        self.assertEqual((u2["count"], u2["source"]), (2, "test"))

    def test_stage1_uses_cache_only_and_bounds_warmup(self):
        fetched, cache = [], {}
        syms = [f"S{i}" for i in range(100)]
        n = fp.warm_screen_cache(syms, cache, 7, lambda s: fetched.append(s) or GOOD, NOW)
        self.assertEqual((n, len(fetched)), (7, 7))
        self.assertEqual(len(fp.rank_candidates(syms, cache, NOW)), 7)                       # unwarmed symbols are not scanned/guessed


# 10. existing features untouched
class T10_ExistingFeaturesIntact(unittest.TestCase):
    ORIG = "/mnt/user-data/uploads/AAA_GitHub_Auto_AI_Advisory_py-main.zip"
    UNTOUCHED = ["main.py", "universe.py", "stocks_buzz.py", "swing_trades.py", "sector_report.py", "ai_engine.py",
                 "data_engine.py", "fundamentals.py", "research_data.py", "nifty500_collector.py", "market_news.py", "config.py"]

    @unittest.skipUnless(os.path.exists(ORIG), "original zip not available")
    def test_unrelated_files_byte_identical(self):
        z = zipfile.ZipFile(self.ORIG)
        base = z.namelist()[0]
        for f in self.UNTOUCHED:
            self.assertEqual(hashlib.sha256(z.read(base + f)).hexdigest(),
                             hashlib.sha256(pathlib.Path(ROOT, f).read_bytes()).hexdigest(), f)

    def test_portfolio_profiles_and_menu_still_defined(self):
        src = pathlib.Path(ROOT, "main.py").read_text(encoding="utf-8")
        names = {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
        for fn in ("build_scan", "main_keyboard", "scan_btn", "run_report", "cmd_report"):
            self.assertIn(fn, names)
        for prof in ("Conservative", "Moderate", "Aggressive"):
            self.assertIn(prof, src)

    def test_public_report_api_unchanged(self):
        self.assertEqual(str(inspect.signature(rg.generate_stock_report_pdf)), "(symbol: str) -> Tuple[bool, str, Optional[str]]")
        sc = rg.assemble(fx.strong(), use_ai=False)["sc"]
        for k in ("pillars", "composite", "coverage", "stance", "action", "veto", "tech_only", "horizons"):
            self.assertIn(k, sc)                                                              # old keys kept for other callers


if __name__ == "__main__":
    unittest.main(verbosity=2)
