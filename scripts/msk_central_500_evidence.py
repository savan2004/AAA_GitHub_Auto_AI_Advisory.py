"""Read-only official Nifty 500 roster + exact-session final NSE EOD probe.

No third-party dependencies, credentials, customer output or database writes.
Membership is the current official CSV at retrieval time, not a historical
membership assertion. Match the exact official symbol, ISIN and trading series.
The audit observes the full roster (500/501 real constituents); it does not
change the central engine's separate 500 regular / 700 total admission caps.
"""
import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError
import zipfile

ROSTERS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
)
ARCHIVES = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day}_F_0000.csv.zip",
    "https://nsearchives.nseindia.in/content/cm/BhavCopy_NSE_CM_0_0_0_{day}_F_0000.csv.zip",
    "https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day}_F_0000.csv.zip",
)
SYMBOL = re.compile(r"[A-Z0-9&._-]{1,30}")
ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, limit):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with build_opener(NoRedirect()).open(req, timeout=10) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("unexpected response")
        data = response.read(limit + 1)
        if not data or len(data) > limit:
            raise ValueError("response size")
        return data


def _roster_details(data):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    names = {str(h).strip().upper(): h for h in reader.fieldnames or []}
    if len(names) != len(reader.fieldnames or []):
        raise ValueError("duplicate roster headers")
    symbol_col = names.get("SYMBOL")
    isin_col = names.get("ISIN CODE") or names.get("ISIN")
    if not symbol_col or not isin_col:
        raise ValueError("missing roster identity columns")
    series_col=names.get("SERIES")
    company_col=names.get("COMPANY NAME")
    result, series, placeholders = {}, {}, []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("invalid roster row")
        sym = row[symbol_col].strip().upper()
        isin = row[isin_col].strip().upper()
        kind = row[series_col].strip().upper() if series_col else "EQ"
        company = row[company_col].strip() if company_col else ""
        # Same explicitly identified non-tradable NSE placeholder as #233.
        if (sym,isin,company,kind)==("DUMMYHEG","DUM545A01024","Dummy HEG Ltd.","EQ"):
            if placeholders:
                raise ValueError("duplicate roster placeholder")
            placeholders.append(sym)
            continue
        if (not SYMBOL.fullmatch(sym) or not ISIN.fullmatch(isin)
                or sym in result or kind not in ("EQ","BE","RR")):
            raise ValueError("invalid or duplicate roster member")
        result[sym] = isin
        series[sym] = kind
        if len(result) > 501:
            raise ValueError("roster exceeds observed official size")
    if len(result) not in (500,501) or len(set(result.values())) != len(result):
        raise ValueError("official roster count or uniqueness invalid")
    # A transitional 501-row roster requires the full official schema.
    if len(result)==501 and not (series_col and company_col):
        raise ValueError("transitional roster missing official metadata")
    return result,series,placeholders


def roster_rows(data):
    return _roster_details(data)[0]


def archive_rows(data, roster, session, *, series=None):
    required = {"TRADDT","TCKRSYMB","SCTYSRS","ISIN","OPNPRIC","HGHPRIC",
                "LWPRIC","CLSPRIC","TTLTRADGVOL"}
    series = series if series is not None else {sym:"EQ" for sym in roster}
    if set(series)!=set(roster) or any(v not in ("EQ","BE","RR") for v in series.values()):
        raise ValueError("invalid roster series")
    accepted, bad, seen, eq_total = set(), set(), set(), 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir() and m.filename.lower().endswith(".csv")]
        if len(members) != 1 or not 0 < members[0].file_size <= 20_000_000:
            raise ValueError("invalid archive shape")
        with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            # Match the proven smart_chart_eod_recovery.py UDiFF normalization.
            # NSE publishes mixed-case fields (TradDt, TckrSymb, SctySrs, ...).
            headers = {str(h).strip().upper() for h in reader.fieldnames or []}
            if len(headers) != len(reader.fieldnames or []):
                raise ValueError("duplicate archive headers")
            if not required.issubset(headers):
                raise ValueError("missing archive fields: " + ",".join(sorted(required-headers)))
            for raw_row in reader:
                if None in raw_row or any(v is None for v in raw_row.values()):
                    raise ValueError("malformed archive row")
                row = {str(k).strip().upper(): str(v).strip() for k,v in raw_row.items()}
                kind=row["SCTYSRS"].strip().upper()
                if kind=="EQ":
                    eq_total += 1
                    if row["TRADDT"].strip() != session.isoformat():
                        raise ValueError("wrong session in official archive")
                sym = row["TCKRSYMB"].strip().upper()
                if sym not in roster or kind!=series[sym]:
                    continue
                if row["TRADDT"].strip() != session.isoformat():
                    raise ValueError("wrong session in official archive")
                if sym in seen:
                    bad.add(sym)
                    continue
                seen.add(sym)
                try:
                    if row["ISIN"].strip().upper() != roster[sym]:
                        raise ValueError("ISIN mismatch")
                    o,h,l,c,v = (Decimal(row[k]) for k in ("OPNPRIC","HGHPRIC","LWPRIC","CLSPRIC","TTLTRADGVOL"))
                    if (not all(x.is_finite() for x in (o,h,l,c,v)) or min(o,h,l,c) <= 0
                        or v < 0 or v != v.to_integral_value()
                        or h < max(o,l,c) or l > min(o,h,c)):
                        raise ValueError("invalid OHLCV")
                    accepted.add(sym)
                except (ValueError, TypeError, InvalidOperation):
                    bad.add(sym)
    if eq_total < 400:
        raise ValueError("implausibly small exchange file")
    return accepted - bad, bad


def probe(session, *, get=fetch, retrieved_at=None):
    report = {"session": session.isoformat(), "retrieved_at": (retrieved_at or datetime.now(timezone.utc)).isoformat(),
              "roster_count": 0, "verified_eod_count": 0, "missing": [], "rejected": [],
              "attempts": [], "status": "withheld"}
    roster = {}
    for url in ROSTERS:
        try:
            data = get(url, 2_000_000)
            roster,series,placeholders = _roster_details(data)
            report.update(roster_count=len(roster), roster_source=url,
                          roster_sha256=hashlib.sha256(data).hexdigest(),
                          nontradable_placeholders=placeholders,
                          non_eq_members=sorted(sym for sym in series if series[sym]!="EQ"),
                          operating_core_limit=500, total_limit=700,
                          admission_status="NOT_EVALUATED")
            break
        except (ValueError, TypeError, KeyError, AttributeError, OSError, URLError, csv.Error, UnicodeError) as exc:
            report["attempts"].append({"source":url, "error":type(exc).__name__,
                                       "detail":str(exc)[:180] if type(exc) is ValueError else None})
    if not roster:
        return report
    report["missing"] = sorted(roster)
    for template in ARCHIVES:
        url = template.format(day=session.strftime("%Y%m%d"))
        try:
            data = get(url, 4_000_000)
            ok, bad = archive_rows(data, roster, session,series=series)
            report.update(verified_eod_count=len(ok), missing=sorted(set(roster)-ok),
                          rejected=sorted(bad), archive_source=url,
                          archive_sha256=hashlib.sha256(data).hexdigest(),
                          status="verified" if len(ok)==len(roster) else "withheld_partial")
            # A valid official file with missing rows is evidence of a gap;
            # don't silently fill it with another date or different source.
            break
        except (ValueError, TypeError, KeyError, AttributeError, OSError, URLError, csv.Error,
                UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
            report["attempts"].append({"source":url, "error":type(exc).__name__,
                                       "detail":str(exc)[:180] if type(exc) is ValueError else None})
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True, type=date.fromisoformat)
    args = p.parse_args()
    if args.session >= datetime.now(timezone.utc).date():
        p.error("probe requires a prior completed date")
    report = probe(args.session)
    print("MSK_500_EVIDENCE " + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
