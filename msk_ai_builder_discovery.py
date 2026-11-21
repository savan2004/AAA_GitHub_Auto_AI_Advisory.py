"""Internal filing discovery. Does not certify statements or authorize publication."""
from dataclasses import dataclass
from datetime import date,datetime,timedelta,timezone
import json
from urllib.parse import urlsplit
from msk_ai_builder_evidence import EvidenceError
from msk_ai_builder_primary import SYMBOL
from msk_ai_builder_filings import check_url

ENDPOINT="https://www.nseindia.in/api/integrated-filing-results"
PAGE="https://www.nseindia.in/companies-listing/corporate-integrated-filing"
IST=timezone(timedelta(hours=5,minutes=30))

@dataclass(frozen=True)
class FilingReference:
    symbol: str
    period_end: date
    scope: str
    broadcast_at: datetime
    revised_at: datetime | None
    url: str

def _time(value):
    if not isinstance(value,str):
        raise EvidenceError("FILING_METADATA_TIME")
    try:
        return datetime.strptime(value.strip(),"%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError as exc:
        raise EvidenceError("FILING_METADATA_TIME") from exc

def parse_listing(payload,*,symbol,period_end,scope,now):
    if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol) or type(period_end) is not date or scope not in ("standalone","consolidated") or not isinstance(now,datetime) or now.utcoffset() is None:
        raise EvidenceError("FILING_METADATA_INPUT")
    if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
        raise EvidenceError("FILING_METADATA_SCHEMA")
    rows=payload["data"]
    # Never silently ignore later pages, which may contain revisions.
    if type(payload.get("totalCount")) is not int or payload["totalCount"]!=len(rows) or not 1<=len(rows)<=20:
        raise EvidenceError("FILING_METADATA_COVERAGE")
    matches=[]
    for row in rows:
        if not isinstance(row,dict) or row.get("symbol")!=symbol:
            raise EvidenceError("FILING_METADATA_IDENTITY")
        try:
            end=datetime.strptime(row["qe_Date"],"%d-%b-%Y").date()
        except (KeyError,TypeError,ValueError) as exc:
            raise EvidenceError("FILING_METADATA_PERIOD") from exc
        if end!=period_end:
            raise EvidenceError("FILING_METADATA_PERIOD")
        basis=str(row.get("consolidated","")).strip().lower()
        if basis not in ("standalone","consolidated"):
            raise EvidenceError("FILING_METADATA_SCOPE")
        if basis!=scope:
            continue
        published=_time(row.get("broadcast_Date"))
        revised_raw=row.get("revised_Date")
        revised=None if revised_raw in (None,"","-") else _time(revised_raw)
        if published.date()<period_end or published>now or (revised and not published<=revised<=now):
            raise EvidenceError("FILING_METADATA_TIME")
        matches.append(FilingReference(symbol,end,basis,published,revised,check_url(row.get("ixbrl"))))
    if not matches:
        raise EvidenceError("FILING_METADATA_NO_MATCH")
    last=max(x.revised_at or x.broadcast_at for x in matches)
    latest={x for x in matches if (x.revised_at or x.broadcast_at)==last}
    if len(latest)!=1:
        raise EvidenceError("FILING_METADATA_AMBIGUOUS_REVISION")
    return latest.pop()

def fetch_listing(symbol,period_end,*,session=None):
    import requests
    if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol) or type(period_end) is not date:
        raise EvidenceError("FILING_METADATA_INPUT")
    own=session is None
    session=session or requests.Session()
    headers={"User-Agent":"Mozilla/5.0","Accept":"application/json","Referer":PAGE}
    try:
        # Ordinary public session, no bypass, credentials or hidden retries.
        with session.get(PAGE,headers={"User-Agent":"Mozilla/5.0"},timeout=(4,8),allow_redirects=False) as warm:
            if warm.status_code!=200 or warm.url!=PAGE:
                raise EvidenceError("FILING_METADATA_SESSION_HTTP_"+str(warm.status_code))
        params={"index":"equities","symbol":symbol,"period_ended":period_end.strftime("%d-%b-%Y"),
                "type":"Integrated Filing- Financials","page":1,"size":20}
        with session.get(ENDPOINT,params=params,headers=headers,timeout=(4,10),allow_redirects=False,stream=True) as response:
            parsed=urlsplit(response.url)
            if response.status_code!=200 or parsed.scheme!="https" or parsed.netloc!="www.nseindia.in" or parsed.path!="/api/integrated-filing-results":
                raise EvidenceError("FILING_METADATA_HTTP_"+str(response.status_code))
            raw=bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                raw.extend(chunk)
                if len(raw)>1_000_000:
                    raise EvidenceError("FILING_METADATA_SIZE")
            return json.loads(raw)
    except EvidenceError:
        raise
    except (requests.RequestException,ValueError,TypeError) as exc:
        raise EvidenceError("FILING_METADATA_UNAVAILABLE") from exc
    finally:
        if own:
            session.close()
