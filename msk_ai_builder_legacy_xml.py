"""Strict legacy Ind AS XBRL quarterly recovery; shadow research only.

Use original exchange bytes, not scraped third-party values. The public
taxonomy references distinguish OneD (quarter) from FourD (year-to-date).
Other taxonomies and historical ISIN changes remain withheld.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from msk_ai_builder_evidence import EvidenceError

XBRLI="http://www.xbrl.org/2003/instance"
ISO="http://www.xbrl.org/2003/iso4217"
MAX_BYTES=2_000_000
HOSTS=("nsearchives.nseindia.com","nsearchives.nseindia.in","archives.nseindia.com")
# The legacy ordinary-company format is distinct from banking/NBFC.
PATH=re.compile(r"/corporate/xbrl/INDAS_[0-9]+_[0-9]+_[0-9]{14}\.xml")
TAXONOMY=re.compile(r"https?://www\.bseindia\.com/xbrl/fin/[0-9]{4}-[0-9]{2}-[0-9]{2}/in-bse-fin")

def check_url(url):
    u=urlsplit(url) if isinstance(url,str) else None
    if not u or u.scheme!="https" or u.netloc not in HOSTS or u.query or u.fragment or not PATH.fullmatch(u.path):
        raise EvidenceError("LEGACY_XML_URL_OR_TAXONOMY")
    return url

def download_xml(url):
    import requests
    check_url(url)
    try:
        with requests.get(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/xml,text/xml"},
                          timeout=(4,10),stream=True,allow_redirects=False) as response:
            if response.status_code!=200 or response.url!=url:
                raise EvidenceError("LEGACY_XML_HTTP")
            if response.headers.get("Content-Type","").split(";")[0].strip().lower() not in ("application/xml","text/xml","application/octet-stream"):
                raise EvidenceError("LEGACY_XML_CONTENT_TYPE")
            body=bytearray()
            for chunk in response.iter_content(16384):
                body.extend(chunk)
                if len(body)>MAX_BYTES:
                    raise EvidenceError("LEGACY_XML_SIZE")
            return bytes(body)
    except EvidenceError:
        raise
    except (requests.RequestException,ValueError,TypeError) as exc:
        raise EvidenceError("LEGACY_XML_UNAVAILABLE") from exc

def extract_quarter(body, member, *, period_end, scope, disseminated_at, now):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BYTES or b"\x00" in body:
        raise EvidenceError("LEGACY_XML_SIZE_OR_ENCODING")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise EvidenceError("LEGACY_XML_UNSAFE")
    if (type(period_end) is not date or period_end>=date(2025,3,31)
            or period_end.month not in (3,6,9,12)
            or scope not in ("standalone","consolidated")):
        raise EvidenceError("LEGACY_XML_PERIOD")
    if not isinstance(disseminated_at,datetime) or disseminated_at.utcoffset() is None or now.utcoffset() is None or disseminated_at>now:
        raise EvidenceError("LEGACY_XML_PUBLICATION")
    try:
        namespaces={}
        for _,(prefix,uri) in ET.iterparse(BytesIO(body),events=("start-ns",)):
            if prefix in namespaces and namespaces[prefix]!=uri:
                raise EvidenceError("LEGACY_XML_NAMESPACE")
            namespaces[prefix]=uri
        root=ET.fromstring(body)
    except ET.ParseError as exc:
        raise EvidenceError("LEGACY_XML_MALFORMED") from exc
    if root.tag!="{"+XBRLI+"}xbrl":
        raise EvidenceError("LEGACY_XML_ROOT")
    taxonomies={uri for uri in namespaces.values() if TAXONOMY.fullmatch(uri)}
    if len(taxonomies)!=1:
        raise EvidenceError("LEGACY_XML_TAXONOMY")
    ns=taxonomies.pop()
    def q(local):return "{"+ns+"}"+local
    def x(local):return "{"+XBRLI+"}"+local
    contexts=[n for n in root.findall(x("context")) if n.get("id")=="OneD"]
    if len(contexts)!=1:
        raise EvidenceError("LEGACY_XML_CONTEXT")
    ctx=contexts[0]
    if any(el.tag.split("}")[-1] in ("segment","scenario","explicitMember","typedMember") for el in ctx.iter()):
        raise EvidenceError("LEGACY_XML_DIMENSION")
    start=date(period_end.year,period_end.month-2,1)
    if (ctx.findtext(x("period")+"/"+x("startDate"))!=start.isoformat()
            or ctx.findtext(x("period")+"/"+x("endDate"))!=period_end.isoformat()):
        raise EvidenceError("LEGACY_XML_CONTEXT_DATE")
    def fact(name):
        nodes=[n for n in root.findall(q(name)) if n.get("contextRef")=="OneD"]
        if len(nodes)!=1 or list(nodes[0]) or not (nodes[0].text or "").strip():
            raise EvidenceError("LEGACY_XML_FIELD_"+name.upper())
        return nodes[0]
    def value(name):return fact(name).text.strip()
    if value("Symbol")!=member.symbol or value("ISIN")!=member.isin:
        raise EvidenceError("LEGACY_XML_IDENTITY")
    if value("NatureOfReportStandaloneConsolidated").lower()!=scope:
        raise EvidenceError("LEGACY_XML_SCOPE")
    if (value("DateOfStartOfReportingPeriod")!=start.isoformat()
            or value("DateOfEndOfReportingPeriod")!=period_end.isoformat()):
        raise EvidenceError("LEGACY_XML_PERIOD")
    if value("DescriptionOfPresentationCurrency")!="INR":
        raise EvidenceError("LEGACY_XML_CURRENCY")
    if value("WhetherResultsAreAuditedOrUnaudited") not in ("Audited","Unaudited"):
        raise EvidenceError("LEGACY_XML_AUDIT")
    try:
        board=date.fromisoformat(value("DateOfBoardMeetingWhenFinancialResultsWereApproved"))
    except ValueError as exc:
        raise EvidenceError("LEGACY_XML_PUBLICATION") from exc
    if not period_end<=board<=disseminated_at.date():
        raise EvidenceError("LEGACY_XML_PUBLICATION")
    def amount(name):
        node=fact(name)
        units=[n for n in root.findall(x("unit")) if n.get("id")==node.get("unitRef")]
        if len(units)!=1 or len(units[0])!=1 or units[0][0].tag!=x("measure"):
            raise EvidenceError("LEGACY_XML_UNIT")
        measure=(units[0][0].text or "").strip().split(":")
        if len(measure)!=2 or namespaces.get(measure[0])!=ISO or measure[1]!="INR":
            raise EvidenceError("LEGACY_XML_UNIT")
        raw=node.text.strip()
        if not re.fullmatch(r"-?[0-9]{1,22}(?:\.[0-9]{1,8})?",raw):
            raise EvidenceError("LEGACY_XML_AMOUNT")
        try:
            result=Decimal(raw)/Decimal(10000000)
        except InvalidOperation as exc:
            raise EvidenceError("LEGACY_XML_AMOUNT") from exc
        return result
    revenue=amount("RevenueFromOperations")
    profit=amount("ProfitLossForPeriod")
    if revenue<=0:
        raise EvidenceError("LEGACY_XML_AMOUNT")
    return revenue,profit

def verify_legacy_quarter(member,evidence,*,period_end,discovery,download=download_xml,clock=lambda:datetime.now(timezone.utc)):
    if (not isinstance(discovery,dict) or discovery.get("status")!="LEGACY_REFERENCES_DISCOVERED_ONLY"
            or (discovery.get("symbol"),discovery.get("isin"),discovery.get("period"),discovery.get("scope"),discovery.get("publication_status"))
            !=(member.symbol,member.isin,period_end.isoformat(),evidence.scope,"WITHHELD")):
        raise EvidenceError("LEGACY_XML_REFERENCE_IDENTITY")
    refs=discovery.get("references")
    if not isinstance(refs,list) or len(refs)!=1:
        raise EvidenceError("LEGACY_XML_REFERENCE_AMBIGUOUS")
    url=check_url(refs[0].get("url"))
    periods=[p for p in evidence.quarterly[:-1] if p.end==period_end]
    if len(periods)!=1 or evidence.symbol!=member.symbol or not 0<=(clock()-evidence.retrieved_at).total_seconds()<=86400:
        raise EvidenceError("LEGACY_XML_SECONDARY_INVALID")
    body=download(url)
    revenue,profit=extract_quarter(body,member,period_end=period_end,scope=evidence.scope,
        disseminated_at=datetime.fromisoformat(refs[0]["disseminated_at"]),now=clock())
    expected=periods[0]
    if abs(revenue-Decimal(str(expected.revenue_cr)))>Decimal("0.51") or abs(profit-Decimal(str(expected.profit_cr)))>Decimal("0.51"):
        raise EvidenceError("LEGACY_XML_VALUE_MISMATCH")
    return {"status":"HISTORICAL_QUARTER_MATCHED_ONLY","symbol":member.symbol,"isin":member.isin,
            "period":period_end.isoformat(),"scope":evidence.scope,"document_sha256":sha256(body).hexdigest(),
            "filing_url":url,"issuer_revenue_cr":str(revenue),"issuer_profit_cr":str(profit),
            "secondary_revenue_cr":str(expected.revenue_cr),"secondary_profit_cr":str(expected.profit_cr),
            "source_format":"legacy_indas_xml_OneD","publication_status":"WITHHELD",
            "limitation":"Legacy quarterly numeric reconciliation only; not full financial or release approval."}
