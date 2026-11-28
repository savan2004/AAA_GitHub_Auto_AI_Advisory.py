"""Strict official public source adapters for internal weekly research staging."""
import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from msk_ai_builder_primary import IndexMember, SYMBOL, ISIN
from msk_ai_builder_evidence import EvidenceError

INDEX_CSV="https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
# The official 27 Nov 2026 CSV contains 501 unique real constituents.
# Accept the observed sizes; never truncate the official roster to 500.
INDEX_SIZES = (500, 501)
CLOSE_CSV="https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
FINAL_ZIP="https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"text/csv,text/plain,*/*",
         "Referer":"https://www.nseindia.com/"}

@dataclass(frozen=True)
class ListedCompany:
    member: IndexMember
    name: str
    sector: str  # Exchange index industry classification
    series: str = "EQ"

@dataclass(frozen=True)
class WeeklyClose:
    symbol: str
    day: date
    price: Decimal
    turnover_lakh: Decimal
    source: str

def _download(url,limit,*,get=None,binary=False):
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
            return bytes(chunks) if binary else bytes(chunks).decode("utf-8-sig")
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
            # Actual NSE roster can include temporary index dummy lines.
            # Skip only the observed, explicitly marked placeholder; all
            # other malformed rows fail closed. Retain real non-EQ members
            # in the universe but exclude them from equity-only screening.
            if (s,i,row["COMPANY NAME"],row["SERIES"])==("DUMMYHEG","DUM545A01024","Dummy HEG Ltd.","EQ"):
                continue
            if row["SERIES"] not in ("EQ","BE","RR") or not SYMBOL.fullmatch(s) or not ISIN.fullmatch(i) or not row["COMPANY NAME"] or not row["INDUSTRY"]:
                raise EvidenceError("INDEX_ROW_INVALID")
            rows.append(ListedCompany(IndexMember(s,i),row["COMPANY NAME"],row["INDUSTRY"],row["SERIES"]))
    except csv.Error as exc:
        raise EvidenceError("OFFICIAL_CSV_ROW") from exc
    if len(rows) not in INDEX_SIZES or len({r.member.symbol for r in rows})!=len(rows) or len({r.member.isin for r in rows})!=len(rows):
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
    wanted={c.member.symbol for c in roster if c.series=="EQ"}
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

def parse_final_closes(payload,day,roster):
    """Parse the final official CM UDiFF archive, matching both symbol and ISIN."""
    if type(day) is not date or not isinstance(payload,bytes) or not 0<len(payload)<=4_000_000:
        raise EvidenceError("FINAL_ARCHIVE_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files=[f for f in archive.infolist() if not f.is_dir()]
            if len(files)!=1 or not files[0].filename.lower().endswith(".csv") or not 0<files[0].file_size<=20*1024*1024:
                raise EvidenceError("FINAL_ARCHIVE_INVALID")
            raw=archive.read(files[0]).decode("utf-8-sig")
        wanted={c.member.symbol:c.member.isin for c in roster if c.series=="EQ"}
        url=FINAL_ZIP.format(d=day.strftime("%Y%m%d"))
        result={}
        for row in _rows(raw,{"TCKRSYMB","SCTYSRS","TRADDT","ISIN","SGMT","CLSPRIC","TTLTRFVAL"}):
            if row["SCTYSRS"]!="EQ" or row["TCKRSYMB"] not in wanted:
                continue
            symbol=row["TCKRSYMB"]
            if row["SGMT"]!="CM" or row["ISIN"]!=wanted[symbol]:
                raise EvidenceError("FINAL_IDENTITY_MISMATCH")
            if date.fromisoformat(row["TRADDT"])!=day:
                raise EvidenceError("EOD_DATE_MISMATCH")
            if symbol in result:
                raise EvidenceError("DUPLICATE_EOD_SYMBOL")
            # UDiFF turnover is in INR; legacy weekly ranking uses lakhs.
            result[symbol]=WeeklyClose(symbol,day,_positive(row["CLSPRIC"]),
                                       _positive(row["TTLTRFVAL"])/Decimal(100000),url)
        if len(result)<400:
            raise EvidenceError("EOD_COVERAGE_INSUFFICIENT")
        return result
    except EvidenceError:
        raise
    except (zipfile.BadZipFile,UnicodeError,csv.Error,ValueError,RuntimeError,OSError,KeyError) as exc:
        raise EvidenceError("FINAL_ARCHIVE_INVALID") from exc


def fetch_closes(day,roster,*,get=None):
    if type(day) is not date:
        raise EvidenceError("EOD_DATE_INVALID")
    roster=tuple(roster)
    url=CLOSE_CSV.format(d=day.strftime("%d%m%Y"))
    try:
        raw=_download(url,20*1024*1024,get=get)
    except EvidenceError as exc:
        # Recover an unavailable transport only; don't conceal contradictory data.
        if str(exc) not in ("OFFICIAL_SOURCE_HTTP","OFFICIAL_SOURCE_UNAVAILABLE"):
            raise
        final_url=FINAL_ZIP.format(d=day.strftime("%Y%m%d"))
        return parse_final_closes(_download(final_url,4_000_000,get=get,binary=True),day,roster)
    return parse_closes(raw,day,roster)
