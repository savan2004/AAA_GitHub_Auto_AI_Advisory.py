"""Strict official public source adapters for internal weekly research staging."""
import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from msk_ai_builder_primary import IndexMember, SYMBOL, ISIN
from msk_ai_builder_evidence import EvidenceError

INDEX_CSV="https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
CLOSE_CSV="https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"text/csv,text/plain,*/*",
         "Referer":"https://www.nseindia.com/"}

@dataclass(frozen=True)
class ListedCompany:
    member: IndexMember
    name: str
    sector: str  # Exchange index industry classification

@dataclass(frozen=True)
class WeeklyClose:
    symbol: str
    day: date
    price: Decimal
    turnover_lakh: Decimal
    source: str

def _download(url,limit,*,get=None):
    import requests
    get=get or requests.get
    try:
        with get(url,headers=HEADERS,timeout=(5,12),allow_redirects=False,stream=True) as response:
            if response.status_code!=200 or response.url!=url:
                raise EvidenceError("OFFICIAL_SOURCE_HTTP")
            chunks=bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(chunks)+len(chunk)>limit:
                    raise EvidenceError("OFFICIAL_SOURCE_SIZE")
                chunks.extend(chunk)
            return bytes(chunks).decode("utf-8-sig")
    except EvidenceError:
        raise
    except (requests.RequestException,UnicodeError,ValueError) as exc:
        raise EvidenceError("OFFICIAL_SOURCE_UNAVAILABLE") from exc

def _rows(raw,required):
    reader=csv.DictReader(io.StringIO(raw),strict=True)
    keys=[(k or "").strip().upper() for k in reader.fieldnames or []]
    if len(keys)!=len(set(keys)) or not required.issubset(keys):
        raise EvidenceError("OFFICIAL_CSV_SCHEMA")
    for item in reader:
        if None in item or any(v is None for v in item.values()):
            raise EvidenceError("OFFICIAL_CSV_ROW")
        yield {k.strip().upper():v.strip() for k,v in item.items()}

def parse_roster(raw):
    rows=[]
    try:
        for row in _rows(raw,{"COMPANY NAME","INDUSTRY","SYMBOL","SERIES","ISIN CODE"}):
            s,i=row["SYMBOL"],row["ISIN CODE"]
            if row["SERIES"]!="EQ" or not SYMBOL.fullmatch(s) or not ISIN.fullmatch(i) or not row["COMPANY NAME"] or not row["INDUSTRY"]:
                raise EvidenceError("INDEX_ROW_INVALID")
            rows.append(ListedCompany(IndexMember(s,i),row["COMPANY NAME"],row["INDUSTRY"]))
    except csv.Error as exc:
        raise EvidenceError("OFFICIAL_CSV_ROW") from exc
    if len(rows)!=500 or len({r.member.symbol for r in rows})!=500 or len({r.member.isin for r in rows})!=500:
        raise EvidenceError("INCOMPLETE_OR_DUPLICATE_INDEX")
    return tuple(sorted(rows,key=lambda r:r.member.symbol))

def fetch_roster(*,get=None):
    return parse_roster(_download(INDEX_CSV,1_000_000,get=get))

def _positive(raw):
    try:
        d=Decimal(raw)
    except InvalidOperation as exc:
        raise EvidenceError("INVALID_EOD_NUMBER") from exc
    if not d.is_finite() or d<=0 or d>Decimal("1e14"):
        raise EvidenceError("INVALID_EOD_NUMBER")
    return d

def parse_closes(raw,day,roster):
    from datetime import datetime
    wanted={c.member.symbol for c in roster}
    url=CLOSE_CSV.format(d=day.strftime("%d%m%Y"))
    output={}
    eq=0
    try:
        for row in _rows(raw,{"SYMBOL","SERIES","DATE1","CLOSE_PRICE","TURNOVER_LACS"}):
            if row["SERIES"]!="EQ":
                continue
            eq+=1
            s=row["SYMBOL"]
            if s not in wanted:
                continue
            if s in output:
                raise EvidenceError("DUPLICATE_EOD_SYMBOL")
            try:
                observed=datetime.strptime(row["DATE1"],"%d-%b-%Y").date()
            except ValueError as exc:
                raise EvidenceError("EOD_DATE_INVALID") from exc
            if observed!=day:
                raise EvidenceError("EOD_DATE_MISMATCH")
            output[s]=WeeklyClose(s,day,_positive(row["CLOSE_PRICE"]),
                                  _positive(row["TURNOVER_LACS"]),url)
    except csv.Error as exc:
        raise EvidenceError("OFFICIAL_CSV_ROW") from exc
    if eq<400 or len(output)<400:
        raise EvidenceError("EOD_COVERAGE_INSUFFICIENT")
    return output

def fetch_closes(day,roster,*,get=None):
    if type(day) is not date:
        raise EvidenceError("EOD_DATE_INVALID")
    url=CLOSE_CSV.format(d=day.strftime("%d%m%Y"))
    return parse_closes(_download(url,20*1024*1024,get=get),day,roster)
