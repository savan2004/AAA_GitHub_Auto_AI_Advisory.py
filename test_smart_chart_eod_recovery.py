"""Synthetic fixtures, no network, no actual trade or price claim."""
import io
import json
import unittest
import zipfile
from datetime import date
import numpy as np
import pandas as pd

from smart_chart_eod_recovery import ArchiveRecovery, final_archive_rows, yahoo_host_history
from smart_chart_fast_scan import choose_auto_chart

DAY = date(2026,11,25)
PREVIOUS = date(2026,11,23)
SYMBOL = "SBIN.NS"


def history():
    idx = pd.bdate_range(end=PREVIOUS, periods=75)
    df = pd.DataFrame({"Open":100., "High":102., "Low":99., "Close":101., "Volume":1000.},index=idx)
    return pd.concat([df,pd.DataFrame(index=pd.DatetimeIndex(["2026-11-24","2026-11-25"]),columns=df.columns)])


def official(close=101.):
    return dict(Open=100., High=103., Low=99., Close=close, Volume=1000., PreviousClose=101.)


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def iter_content(self,chunk_size):
        yield self.body


class RecoveryTests(unittest.TestCase):
    def test_bulk_archive_only_two_fetches_and_real_session_after_holiday(self):
        calls = []
        syms = [SYMBOL,"TCS.NS"]
        def fetch(day,wanted):
            calls.append(day)
            self.assertEqual(set(wanted),set(syms))
            prior = dict(official(),High=102.)
            return {s:(prior if day == PREVIOUS else official(102.)) for s in syms}
        recover = ArchiveRecovery(syms, DAY, fetch=fetch)
        for sym in syms:
            got = recover(sym,history())
            self.assertIsNotNone(got)
            self.assertEqual(str(got.index[-1].date()),str(DAY))
            self.assertEqual(got.Close.iloc[-1],102.)
        self.assertEqual(calls,[DAY,PREVIOUS])
        self.assertEqual(recover.recovered,2)

    def test_year_boundary_does_not_reject_real_older_candles(self):
        old = history().iloc[:1].copy()
        old.index = pd.DatetimeIndex(["2025-12-01"])
        frame = pd.concat([old,history()])
        def fetch(day,wanted):
            return {SYMBOL:(dict(official(),High=102.) if day == PREVIOUS else official(102.))}
        recovered = ArchiveRecovery([SYMBOL],DAY,fetch=fetch)(SYMBOL,frame)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.index[0].year,2025)

    def test_individual_fetch_keeps_exact_ticker_and_never_uses_stale(self):
        from smart_chart_eod_recovery import individual_chart_history
        calls = []
        def fetch(sym):
            calls.append(sym)
            return history()
        def alternate(sym):
            calls.append(sym)
            fresh = history().drop(pd.Timestamp("2026-11-24"))
            fresh.loc[pd.Timestamp(DAY)] = [100.,103.,99.,102.,1000.]
            return fresh
        result = individual_chart_history("ASIANPAINT.NS",DAY,fetch=fetch,alternate=alternate,
                                          recovery=lambda *_:None)
        self.assertEqual(calls,["ASIANPAINT.NS","ASIANPAINT.NS"])
        self.assertEqual(result.Close.iloc[-1],102.)
        self.assertIsNone(individual_chart_history("ASIANPAINT.NS",DAY,fetch=fetch,
                                                    recovery=lambda *_:None))

    def test_individual_repair_preserves_official_provenance_and_validates_result(self):
        from smart_chart_eod_recovery import individual_chart_history
        stale = history()
        stale.attrs["history_source"] = "Yahoo"
        def fetch(day, wanted):
            return {SYMBOL: (dict(official(), High=102.) if day == PREVIOUS else official(102.))}
        archive = ArchiveRecovery([SYMBOL], DAY, fetch=fetch)
        result = individual_chart_history(SYMBOL, DAY, fetch=lambda sym: stale,
                                          recovery=archive)
        self.assertIsNotNone(result)
        self.assertEqual(result.attrs["history_source"], "Yahoo individual + NSE final archive")
        self.assertEqual(result.Close.iloc[-1], 102.)
        self.assertIsNone(individual_chart_history(SYMBOL, DAY, fetch=lambda sym: stale,
                                                    recovery=lambda sym, frame: stale))

    def test_price_missing_with_zero_volume_or_verified_positive_volume(self):
        def fetch(day,wanted):
            return {SYMBOL:(dict(official(),High=102.) if day == PREVIOUS else official(102.))}
        for volume in (0.,1000.,999.):
            frame = history()
            frame.loc[pd.Timestamp(DAY),"Volume"] = volume
            result = ArchiveRecovery([SYMBOL],DAY,fetch=fetch)(SYMBOL,frame)
            if volume == 999.:
                self.assertIsNone(result)
            else:
                self.assertIsNotNone(result)
                self.assertEqual(result.Volume.iloc[-1],1000.)

    def test_mismatch_partial_latest_and_four_missing_sessions_fail(self):
        def fetch(day,wanted):
            return {SYMBOL:dict(official(), High=102., Volume=2000.)}
        self.assertIsNone(ArchiveRecovery([SYMBOL],DAY,fetch=fetch)(SYMBOL,history()))
        calls = []
        def forbidden(*a):
            calls.append(a)
            return {}
        partial = history()
        partial.loc[pd.Timestamp(DAY),"Open"] = -1.
        recover = ArchiveRecovery([SYMBOL],DAY,fetch=forbidden)
        self.assertIsNone(recover(SYMBOL,partial))
        self.assertIsNone(recover(SYMBOL,history().drop(pd.DatetimeIndex(["2026-11-23", "2026-11-20", "2026-11-19"]))))
        self.assertEqual(calls,[])

    def test_two_missing_sessions_recover_from_one_shared_older_archive(self):
        older = date(2026, 11, 20)
        calls = []
        def fetch(day, wanted):
            calls.append(day)
            return {symbol: (official(102.) if day == DAY else dict(official(), High=102.))
                    for symbol in wanted}
        recover = ArchiveRecovery([SYMBOL, "TCS.NS"], DAY, fetch=fetch)
        frame = history()
        frame.loc[pd.Timestamp(PREVIOUS), ["Open", "High", "Low", "Close"]] = np.nan
        for symbol in (SYMBOL, "TCS.NS"):
            result = recover(symbol, frame)
            self.assertIsNotNone(result)
            self.assertEqual([t.date() for t in result.index[-3:]], [older, PREVIOUS, DAY])
            self.assertEqual(result.Close.iloc[-1], 102.)
        self.assertEqual(calls, [DAY, PREVIOUS, older])
        frame.loc[pd.Timestamp(PREVIOUS), "Close"] = 100.
        self.assertIsNone(recover(SYMBOL, frame))

    def test_three_missing_sessions_keep_date_and_full_overlap_contract(self):
        days = [date(2026, 11, number) for number in (19, 20, 23, 25)]
        frame = history().drop(pd.DatetimeIndex(days[1:3]))
        calls = []
        def fetch(day, wanted):
            calls.append(day)
            return {SYMBOL: dict(official(), High=102.)}
        recover = ArchiveRecovery([SYMBOL], DAY, fetch=fetch)
        result = recover(SYMBOL, frame)
        self.assertIsNotNone(result)
        self.assertEqual([stamp.date() for stamp in result.index[-4:]], days)
        self.assertEqual(calls, list(reversed(days)))

    def test_partial_observed_prices_must_match_official_candle(self):
        def fetch(day, wanted):
            return {SYMBOL: (official(102.) if day == DAY else dict(official(), High=102.))}
        recover = ArchiveRecovery([SYMBOL], DAY, fetch=fetch)
        frame = history()
        frame.loc[pd.Timestamp(DAY), ["Open", "High", "Low", "Close"]] = [100., 103., 99., 102.]
        self.assertIsNotNone(recover(SYMBOL, frame))
        frame.loc[pd.Timestamp(DAY), "Open"] = 101.
        self.assertIsNone(recover(SYMBOL, frame))
        self.assertIn("latest_price_mismatch_Open", recover.rejection_reasons)

    def test_adjusted_price_mismatch_and_volume_mismatch_have_exact_reasons(self):
        from smart_chart_eod_recovery import stitch_final_day, prior_history
        base = prior_history(history(), DAY, PREVIOUS)
        prior = dict(official(),High=102.)
        latest = official(102.)
        adjusted = base.copy()
        adjusted[["Open","High","Low","Close"]] *= .98
        diagnostics = {}
        self.assertIsNone(stitch_final_day(adjusted, PREVIOUS, DAY, prior, latest,
                                           diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"prior_price_mismatch_Open_High_Low_Close":1})
        diagnostics = {}
        self.assertIsNone(stitch_final_day(base, PREVIOUS, DAY, dict(prior,Volume=1001.), latest,
                                           diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"prior_volume_mismatch":1})
        self.assertIsNotNone(stitch_final_day(base, PREVIOUS, DAY, prior, latest))

    def test_final_zip_validates_date_duplicate_and_exact_symbol(self):
        def make(day, duplicate=False):
            header = "TckrSymb,SctySrs,TradDt,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol\n"
            line = f"SBIN,EQ,{day},100,103,99,102,101,1000\n"
            lines = [line] * (2 if duplicate else 1)
            lines += [f"LONGDUMMYNAME{i:06d},EQ,{day},100,103,99,102,101,1000\n" for i in range(1500)]
            z=io.BytesIO()
            with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("final.csv",header+"".join(lines))
            return z.getvalue()
        got=final_archive_rows(DAY,[SYMBOL],get=lambda *a,**k:Response(make(DAY)))
        self.assertEqual(got[SYMBOL]["Close"],102.)
        self.assertEqual(final_archive_rows(DAY,[SYMBOL],get=lambda *a,**k:Response(make(PREVIOUS))),{})
        self.assertEqual(final_archive_rows(DAY,[SYMBOL],get=lambda *a,**k:Response(make(DAY,True))),{})

    def test_yahoo_second_host_requires_current_correct_symbol(self):
        dates = pd.bdate_range(end=DAY,periods=80,tz="Asia/Kolkata")
        item={"meta":{"symbol":SYMBOL,"currency":"INR","exchangeTimezoneName":"Asia/Kolkata","dataGranularity":"1d"},
              "timestamp":[int(t.timestamp()) for t in dates],
              "indicators":{"quote":[{"open":[100.]*80,"high":[102.]*80,"low":[99.]*80,"close":[101.]*80,"volume":[1000.]*80}]}}
        calls=[]
        def get(url,**kwargs):
            calls.append(url)
            if "query1" in url:
                return Response(b"",503)
            return Response(json.dumps({"chart":{"error":None,"result":[item]}}).encode())
        result=yahoo_host_history(SYMBOL,DAY,get=get)
        self.assertEqual(result.Close.iloc[-1],101.)
        self.assertEqual(len(calls),2)
        self.assertIn("query2",result.attrs["history_source"])
        item["meta"]["symbol"]="TCS.NS"
        self.assertIsNone(yahoo_host_history(SYMBOL,DAY,get=get))

    def test_scanner_calls_recovery_before_scoring(self):
        def fetch(day,wanted):
            return {SYMBOL:(dict(official(),High=102.) if day == PREVIOUS else official(102.))}
        recover=ArchiveRecovery([SYMBOL],DAY,fetch=fetch)
        def scorer(sym,name,*,df):
            self.assertEqual(str(df.index[-1].date()),str(DAY))
            return {"sym":sym,"close":float(df.Close.iloc[-1]),"score":15,"setup_confirmed":True}
        result=choose_auto_chart([(SYMBOL,"SBI")],download=lambda **kw:history(),
            scorer=scorer,session_key=str(DAY),scan_all=True,recover_history=recover,
            min_score=12,require_confirmed_setup=True)
        self.assertEqual(result[0]["_history_source"],"Yahoo history + NSE final EOD")
        self.assertEqual(result[2],1)

if __name__ == "__main__":
    unittest.main()
