"""Fail-closed automatic Portfolio Builder from an OPERATOR-APPROVED research artifact.

Never reads the internal SECONDARY_ONLY weekly staging bank as customer research.
No stock choice, percentage input, database writes, market orders or Telegram I/O.
The source producer and regulatory approval are separate release prerequisites.
"""
import json
import os
import re
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
from msk_ai_builder_profile import ResearchProfile
from msk_ai_builder_research_release import require_reviewed_research


class ResearchUnavailable(ValueError):
    """A publishable, verified batch is not available for the requested profile."""


_NSE_HOSTS = frozenset((
    "www.nseindia.com",
    "nseindia.com",
    "nsearchives.nseindia.com",
    "archives.nseindia.com",
    "nsearchives.nseindia.in",
    "archives.nseindia.in",
))
_SHA256 = re.compile(r"[a-fA-F0-9]{64}")
_SYMBOL = re.compile(r"[A-Z0-9&-]{2,20}")


def automatic_enabled(environ=None):
    env = os.environ if environ is None else environ
    return env.get("MSK_BUILDER_V2_ENABLED") == "1"


def _source_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid source URL")
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname not in _NSE_HOSTS
            or url.username is not None or url.password is not None
            or url.port is not None or not url.path.strip("/")
            or url.fragment):
        raise ValueError("non-official source URL")
    return value


def _candidate(row, session):
    if not isinstance(row, dict):
        raise ValueError("invalid company")
    if (row["evidence_status"] != "ISSUER_AUTHENTICATED"
            or row["price_status"] != "EXCHANGE_VERIFIED"
            or row["valuation_status"] != "VERIFIED"
            or row["risk_status"] != "VERIFIED"
            or row["corporate_actions_status"] != "VERIFIED"
            or type(row["issuer_identity_verified"]) is not bool
            or row["issuer_identity_verified"] is not True
            or type(row["verified_annual_periods"]) is not int
            or row["verified_annual_periods"] < 5
            or type(row["verified_quarter_periods"]) is not int
            or row["verified_quarter_periods"] < 8
            or date.fromisoformat(row["close_date"]) != session
            or not _SHA256.fullmatch(row["filing_sha256"])):
        raise ValueError("incomplete issuer/price verification")
    _source_url(row["filing_url"])
    _source_url(row["price_source_url"])
    symbol = row["symbol"]
    if (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
            or symbol.startswith(("DEMO", "TEST", "SYNTH"))):
        raise ValueError("invalid or demonstration symbol")
    score = Decimal(str(row["score"]))
    if not score.is_finite() or not Decimal(75) <= score <= Decimal(100):
        raise ValueError("candidate below quality threshold")
    return ApprovedCandidate(
        symbol,
        row["isin"],
        row["sector"],
        session,
        Decimal(str(row["close_rupees"])),
        score,
    )


def approved_plan(profile, raw, *, latest_session):
    """Validate immutable, dated evidence labels BEFORE allocating any capital.

    APPROVED is a downstream operator/publication decision; this function cannot
    authenticate exchange documents on its own and must not publish staging data.
    """
    try:
        if not isinstance(profile, ResearchProfile) or type(latest_session) is not date:
            raise ValueError("invalid profile")
        if not isinstance(raw, str) or not 0 < len(raw.encode("utf-8")) <= 64_000:
            raise ValueError("no approved research")
        payload = json.loads(raw, parse_float=Decimal,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(payload, dict)
                or type(payload.get("schema_version")) is not int
                or payload["schema_version"] != 1
                or payload["publication_status"] != "APPROVED"
                or payload["source_class"] != "ISSUER_PRIMARY_VERIFIED"
                or payload["regulatory_review_status"] != "APPROVED"):
            raise ValueError("research not approved")
        session = date.fromisoformat(payload["close_date"])
        # A weekly list may be used; closing prices still require an exact
        # completed NSE session match before a customer portfolio is generated.
        if session != latest_session:
            raise ValueError("stale closing prices")
        rows = payload["companies"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError("no approved eligible companies")
        require_reviewed_research(raw, latest_session=latest_session, companies=rows)
        candidates = [_candidate(row, session) for row in rows]
        plan = allocate_portfolio(profile, candidates, expected_session=session)
        if not plan.positions:
            raise ValueError("no eligible positions under this risk preference")
        if plan.cash + sum((p.cost for p in plan.positions), Decimal(0)) != Decimal(profile.capital_rupees):
            raise ValueError("capital reconciliation failed")
        return plan, payload
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, OverflowError):
        raise ResearchUnavailable("NO_VERIFIED_PORTFOLIO_AVAILABLE") from None


def _completed_session():
    from portfolio_doctor_live import PortfolioDoctorError, expected_doctor_session
    try:
        return expected_doctor_session()
    except PortfolioDoctorError:
        raise ResearchUnavailable("MARKET_CALENDAR_UNAVAILABLE") from None


def automatic_available(environ=None):
    """Do not invite customer inputs before an approved real-data batch exists."""
    env = os.environ if environ is None else environ
    if not automatic_enabled(env):
        return False
    try:
        approved_plan(
            ResearchProfile(1_000_000, "medium", 36),
            env.get("MSK_BUILDER_V2_APPROVED_RESEARCH"),
            latest_session=_completed_session(),
        )
        return True
    except ResearchUnavailable:
        return False


def build_automatic_pdf(capital, risk, months, *, environ=None, latest_session=None):
    """Return a validated 3-page+ PDF; no fake, guessed or customer-picked stocks."""
    env = os.environ if environ is None else environ
    if not automatic_enabled(env):
        raise ResearchUnavailable("AUTOMATIC_BUILDER_DISABLED")
    try:
        profile = ResearchProfile(capital, risk, months)
    except ValueError:
        raise ResearchUnavailable("INVALID_PROFILE") from None
    plan, artifact = approved_plan(
        profile, env.get("MSK_BUILDER_V2_APPROVED_RESEARCH"),
        latest_session=latest_session or _completed_session(),
    )
    from msk_ai_builder_infographic import allocation_pdf
    try:
        pdf = allocation_pdf(plan, artifact, fictional=False)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise ResearchUnavailable("PDF_EVIDENCE_INVALID") from None
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-") or len(pdf) < 1_000:
        raise ResearchUnavailable("PDF_EVIDENCE_INVALID")
    return pdf
