"""Read an explicitly published V2 research artifact. Staging is separate."""
import json
import os
from html import escape
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
from msk_ai_builder_profile import ResearchProfile


class ResearchUnavailable(ValueError):
    """Public-safe status for missing or unsuitable published research."""


def automatic_enabled(environ=None):
    env = os.environ if environ is None else environ
    return env.get("MSK_BUILDER_V2_ENABLED") == "1"


def approved_plan(profile, raw, *, latest_session, optimize=False):
    """Check dates and evidence labels before accepting an operator artifact."""
    try:
        if not isinstance(raw, str) or not 0 < len(raw) <= 64_000:
            raise ValueError
        payload = json.loads(raw, parse_float=Decimal)
        if (payload["schema_version"] != (2 if optimize else 1) or payload["publication_status"] != "APPROVED"
                or type(latest_session) is not date):
            raise ValueError
        session = date.fromisoformat(payload["close_date"])
        if not latest_session - timedelta(days=7) <= session <= latest_session:
            raise ValueError
        rows = payload["companies"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError

        candidates = []
        for row in rows:
            if (row["evidence_status"] != "PRIMARY_RECONCILED"
                    or row["price_status"] != "EXCHANGE_VERIFIED"
                    or date.fromisoformat(row["close_date"]) != session):
                raise ValueError
            for key in ("filing_url", "price_source_url"):
                url = urlsplit(row[key])
                if (url.scheme != "https" or url.hostname not in
                        {"www.nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com"}
                        or not url.path.strip("/") or url.username is not None or url.port is not None):
                    raise ValueError
            candidates.append(ApprovedCandidate(
                row["symbol"], row["isin"], row["sector"], session,
                Decimal(row["close_rupees"]), Decimal(row["score"]),
            ))
        history = payload["risk_history"] if optimize else None
        if optimize and not isinstance(history, dict):
            raise ValueError
        return allocate_portfolio(profile, candidates, expected_session=session, risk_history=history)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        raise ResearchUnavailable("Approved research unavailable") from None


def load_approved_model(capital, risk, horizon):
    """Read once, validate once, and retain the matching source artifact."""
    if not automatic_enabled():
        raise ResearchUnavailable("Automatic Builder is disabled")
    from portfolio_doctor_live import PortfolioDoctorError, expected_doctor_session

    try:
        latest = expected_doctor_session()
    except PortfolioDoctorError:
        raise ResearchUnavailable("Market calendar unavailable") from None
    profile = ResearchProfile(capital, risk, horizon)
    optimize = os.getenv("MSK_BUILDER_V2_OPTIMIZER_ENABLED") == "1"
    raw = os.getenv("MSK_BUILDER_V2_APPROVED_RESEARCH")
    plan = approved_plan(profile, raw, latest_session=latest, optimize=optimize)
    return plan, json.loads(raw, parse_float=Decimal), optimize


def build_automatic_report(capital, risk, horizon):
    plan, _, optimize = load_approved_model(capital, risk, horizon)
    lines = [
        "<b>Portfolio Builder V2 · Research model</b>",
        f"Capital ₹{capital:,} | {risk.title()} risk preference | {horizon} months",
        f"Completed NSE closes: {plan.close_date.isoformat()}",
        "",
    ]
    if not plan.positions:
        lines.append("No stock fit the model limits. The full amount remains as modeled cash.")
    for row in plan.positions:
        lines.append(
            f"<b>{escape(row.candidate.symbol)}</b> · {row.shares:,} shares × "
            f"₹{row.candidate.close_rupees:,.2f} · {100 * row.cost / capital:.2f}%"
        )
    if optimize and plan.positions:
        lines.append("Method: constrained minimum variance using approved historical returns; not a forecast.")
    lines.extend(["", f"Modeled cash: ₹{plan.cash:,.2f} ({100 * plan.cash / capital:.2f}%)",
                  "Hypothetical allocation at dated closes. No orders are placed."])
    return "\n".join(lines)
