"""Internal filing discovery. Does not certify statements or authorize publication."""
from dataclasses import dataclass
from datetime import date,datetime,timedelta,timezone
import json
import re
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
        revised_raw=row.get("revised_Date")
        revised=None if revised_raw in (None,"","-") else _time(revised_raw)
        broadcast_raw=row.get("broadcast_Date")
        if broadcast_raw in (None,"","-") and revised is not None:
            # NSE's revised-file entry may omit its original broadcast date.
            # Resolve ONLY if this same response contains exactly one original
            # publication for this symbol, period and accounting basis.
            originals=[other for other in rows if other is not row
                       and isinstance(other,dict)
                       and other.get("symbol")==symbol
                       and other.get("qe_Date")==row["qe_Date"]
                       and str(other.get("consolidated","")).strip().lower()==basis
                       and other.get("broadcast_Date") not in (None,"","-")
                       and other.get("revised_Date") in (None,"","-")]
            if len(originals)!=1:
                raise EvidenceError("FILING_METADATA_REVISION_UNLINKED")
            broadcast_raw=originals[0]["broadcast_Date"]
        published=_time(broadcast_raw)
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

# NSE's separate pre-March-2025 quarterly financial-results feed.
LEGACY_PAGE="https://www.nseindia.in/companies-listing/corporate-filings-financial-results"
LEGACY_ENDPOINT="https://www.nseindia.in/api/corporates-financial-results"


def parse_legacy_listing(payload, *, member, period_end, scope, now):
    """Find exact old XBRL references; this is NOT financial verification.

    Do not mix consolidated and standalone, or accept cumulative statements,
    foreign identities, ambiguous revisions or unchecked document hosts.
    """
    from msk_ai_builder_primary import IndexMember
    if (not isinstance(member,IndexMember) or type(period_end) is not date
            or not period_end<date(2025,3,31) or scope not in ("standalone","consolidated")
            or not isinstance(now,datetime) or now.utcoffset() is None):
        raise EvidenceError("LEGACY_METADATA_INPUT")
    if not isinstance(payload,list) or not 1<=len(payload)<=1000:
        raise EvidenceError("LEGACY_METADATA_COVERAGE")
    matches=[]
    for row in payload:
        if not isinstance(row,dict):
            raise EvidenceError("LEGACY_METADATA_SCHEMA")
        if row.get("symbol")!=member.symbol:
            continue
        try:
            end=datetime.strptime(row["toDate"],"%d-%b-%Y").date()
        except (ValueError,KeyError,TypeError) as exc:
            raise EvidenceError("LEGACY_METADATA_PERIOD") from exc
        if end!=period_end:
            continue
        basis={"Consolidated":"consolidated","Non-Consolidated":"standalone"}.get(row.get("consolidated"))
        if basis!=scope or row.get("period")!="Quarterly" or row.get("cumulative")!="Non-cumulative":
            continue
        if row.get("isin")!=member.isin:
            raise EvidenceError("LEGACY_METADATA_IDENTITY")
        try:
            start=datetime.strptime(row["fromDate"],"%d-%b-%Y").date()
        except (ValueError,KeyError,TypeError) as exc:
            raise EvidenceError("LEGACY_METADATA_PERIOD") from exc
        if end.month not in (3,6,9,12) or start!=date(end.year,end.month-2,1):
            raise EvidenceError("LEGACY_METADATA_PERIOD")
        published=_time(row.get("broadCastDate"))
        disseminated=_time(row.get("exchdisstime"))
        if not end<=published.date() or not published<=disseminated<=now:
            raise EvidenceError("LEGACY_METADATA_TIME")
        url=row.get("xbrl")
        u=urlsplit(url) if isinstance(url,str) else None
        if (u is None or u.scheme!="https" or u.netloc not in ("nsearchives.nseindia.com","nsearchives.nseindia.in","archives.nseindia.com")
                or u.query or u.fragment or not re.fullmatch(r"/corporate/xbrl/[A-Za-z0-9_-]+\.xml",u.path)):
            raise EvidenceError("LEGACY_METADATA_URL")
        matches.append((disseminated,url))
    if not matches:
        raise EvidenceError("LEGACY_METADATA_NO_MATCH")
    # Retain all matching source references, but never choose among
    # competing documents automatically; the old API has no proven revision
    # completeness contract. A later numeric verifier must resolve these.
    unique=sorted(set(matches),reverse=True)
    return {"status":"LEGACY_REFERENCES_DISCOVERED_ONLY","symbol":member.symbol,
            "isin":member.isin,"period":period_end.isoformat(),"scope":scope,
            "references":[{"url":url,"disseminated_at":stamp.isoformat()} for stamp,url in unique],
            "publication_status":"WITHHELD"}


def fetch_legacy_listing(symbol, *, session=None):
    """Bounded official old-feed lookup; no guessed URLs or paid APIs."""
    import requests
    if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol):
        raise EvidenceError("LEGACY_METADATA_INPUT")
    own=session is None
    session=session or requests.Session()
    headers={"User-Agent":"Mozilla/5.0","Accept":"application/json","Referer":LEGACY_PAGE}
    try:
        with session.get(LEGACY_PAGE,headers={"User-Agent":"Mozilla/5.0"},timeout=(4,8),allow_redirects=False) as warm:
            if warm.status_code!=200 or warm.url!=LEGACY_PAGE:
                raise EvidenceError("LEGACY_METADATA_SESSION_HTTP_"+str(warm.status_code))
        with session.get(LEGACY_ENDPOINT,params={"index":"equities","period":"Quarterly","symbol":symbol},
                         headers=headers,timeout=(4,10),allow_redirects=False,stream=True) as response:
            parsed=urlsplit(response.url)
            if response.status_code!=200 or parsed.scheme!="https" or parsed.netloc!="www.nseindia.in" or parsed.path!="/api/corporates-financial-results":
                raise EvidenceError("LEGACY_METADATA_HTTP_"+str(response.status_code))
            raw=bytearray()
            for chunk in response.iter_content(16384):
                raw.extend(chunk)
                if len(raw)>2_000_000:
                    raise EvidenceError("LEGACY_METADATA_SIZE")
            return json.loads(raw)
    except EvidenceError:
        raise
    except (requests.RequestException,ValueError,TypeError) as exc:
        raise EvidenceError("LEGACY_METADATA_UNAVAILABLE") from exc
    finally:
        if own:
            session.close()


def discover_legacy_quarter(member, evidence, *, period_end):
    return parse_legacy_listing(fetch_legacy_listing(member.symbol),member=member,
                                period_end=period_end,scope=evidence.scope,
                                now=datetime.now(timezone.utc))
