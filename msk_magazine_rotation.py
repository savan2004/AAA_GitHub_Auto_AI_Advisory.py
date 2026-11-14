"""Draft-only, source-bound editorial rotation for MSK Monthly Outlook.

This module has NO network access, Telegram hooks, DB writes, scheduling or publishing.
The caller must supply immutable published-volume history from durable storage.
Do not describe a planned topic or source record as an independently verified article.
"""
from collections import Counter
from datetime import date, timedelta
from difflib import SequenceMatcher
from hashlib import sha256
import re
from urllib.parse import urlsplit

MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
ANGLES = (
    "Practical worked example", "When the strategy fails", "Market-regime comparison",
    "Transaction costs and slippage", "Risk-managed case study", "Historical evidence audit",
    "Common retail misconceptions", "Stress test and invalidation",
)
FORMATS = (
    "Research explainer", "Case-study laboratory",
    "Evidence-versus-myth", "Regime and risk review",
)
# Finite, curated seed library; extend with reviewed themes before it is exhausted.
# Slug, category, display title. No implied trading edge.
TOPICS = (
    ("trend_ema_adx", "trend", "Trend following with EMA and ADX"),
    ("vwap_regimes", "trend", "VWAP in trending and range-bound sessions"),
    ("breakout_quality", "trend", "Breakouts versus false breakouts"),
    ("multi_timeframe", "trend", "Multiple-timeframe confirmation"),
    ("regression_channel", "trend", "Regression channels and trend exhaustion"),
    ("relative_strength", "trend", "Relative strength and sector leadership"),
    ("mean_reversion", "trend", "Mean reversion and its failure modes"),
    ("volatility_stop", "risk", "ATR stops and position sizing"),
    ("drawdown", "risk", "Managing drawdown and sequence risk"),
    ("position_sizing", "risk", "Risk budgeting across correlated positions"),
    ("gap_risk", "risk", "Overnight gap and execution risk"),
    ("liquidity", "risk", "Liquidity, spreads and realistic execution"),
    ("greeks_delta", "options", "Options Greek Delta and directional exposure"),
    ("greeks_gamma", "options", "Gamma risk around expiry"),
    ("greeks_theta", "options", "Theta decay versus implied volatility"),
    ("options_iv", "options", "Implied versus realized volatility"),
    ("option_spreads", "options", "Defined-risk option spreads"),
    ("open_interest", "options", "Open interest: what it can and cannot prove"),
    ("volume_delta", "order_flow", "Trade-level volume delta versus candle estimates"),
    ("cvd", "order_flow", "Cumulative volume delta and data-quality limits"),
    ("market_profile", "order_flow", "Auction market theory and value areas"),
    ("orderbook", "order_flow", "Order-book imbalance and spoofing limitations"),
    ("market_breadth", "market", "Breadth participation and index divergence"),
    ("sector_rotation", "market", "Sector rotation across market regimes"),
    ("fii_dii", "market", "Institutional flows versus price action"),
    ("event_volatility", "market", "Volatility around macroeconomic events"),
    ("factor_value", "fundamental", "Value factors and value traps"),
    ("earnings_quality", "fundamental", "Cash-flow quality versus accounting earnings"),
    ("quality_momentum", "fundamental", "Quality and momentum combination"),
    ("backtest_bias", "quant", "Look-ahead and survivorship bias in backtests"),
    ("walk_forward", "quant", "Walk-forward validation and overfitting"),
    ("slippage_models", "quant", "Slippage-aware strategy evaluation"),
)

def first_monday(year, month):
    """First Monday date, regardless of exchange holidays."""
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7)

def _month(issue):
    if not isinstance(issue, str) or not MONTH.fullmatch(issue):
        raise ValueError("Issue must use YYYY-MM")
    return date.fromisoformat(issue + "-01")

def _history(history):
    if not isinstance(history, list):
        raise ValueError("A durable published-volume history list is required")
    seen_issues, seen_keys = set(), set()
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("Invalid published-volume history record")
        issue, key = entry.get("issue"), entry.get("article_key")
        _month(issue)
        if not isinstance(key, str) or not key.strip():
            raise ValueError("History entry lacks article key")
        if issue in seen_issues or key in seen_keys:
            raise ValueError("History contains duplicate issue or article key")
        seen_issues.add(issue)
        seen_keys.add(key)
    return sorted(history, key=lambda record: record["issue"])

def plan_volume(issue, history, *, refresh_every=15):
    """Pick a distinct topic+angle, rotate style every 12-15 published volumes.

    Never recycles a previously published topic+angle pair. Exhaustion raises.
    Refresh changes the editorial format automatically, not historic content.
    """
    month = _month(issue)
    if type(refresh_every) is not int or not 12 <= refresh_every <= 15:
        raise ValueError("Refresh interval must be an integer from 12 to 15")
    history = _history(history)
    if history and issue <= history[-1]["issue"]:
        raise ValueError("Issue is already published or predates the publication ledger")

    number = len(history) + 1
    cycle = (number - 1) // refresh_every
    used_keys = {entry["article_key"] for entry in history}
    by_theme = Counter(entry["article_key"].split("/", 1)[0] for entry in history)
    recent_themes = {entry["article_key"].split("/", 1)[0] for entry in history[-6:]}
    categories = {slug: category for slug, category, _ in TOPICS}
    recent_categories = [categories.get(entry["article_key"].split("/", 1)[0])
                         for entry in history[-3:]]
    candidates = []
    for index, (slug, category, title) in enumerate(TOPICS):
        for offset in range(len(ANGLES)):
            angle_index = (cycle + index + offset) % len(ANGLES)
            angle = ANGLES[angle_index]
            key = slug + "/" + str(angle_index)
            if key in used_keys:
                continue
            sort = (
                int(slug in recent_themes),
                recent_categories.count(category),
                by_theme[slug],
                offset,
                index,
            )
            candidates.append((sort, key, slug, category, title, angle))
    if not candidates:
        raise RuntimeError("Editorial library exhausted: add reviewed themes; never repeat articles")
    _, key, slug, category, title, angle = min(candidates, key=lambda item: item[0])
    return {
        "issue": issue,
        "volume": number,
        "publish_target": first_monday(month.year, month.month).isoformat(),
        "article_key": key, "topic_id": slug, "category": category,
        "title": title, "angle": angle,
        "format": FORMATS[cycle % len(FORMATS)],
        "editorial_cycle": cycle + 1,
        "style_refreshed": number > 1 and (number - 1) % refresh_every == 0,
        "approved": False,
        "status": "PLANNED_REQUIRES_SOURCE_EVIDENCE",
    }

def draft_article(plan, source_facts, history):
    """Make an UNAPPROVED evidence-labelled article from supplied dated facts.

    Source URLs are only syntactically checked, not independently fetched/verified.
    The editor must verify factual accuracy and originality before publication.
    """
    published = _history(history)
    if not isinstance(plan, dict) or plan.get("approved") is not False:
        raise ValueError("Only a planned, unapproved article can be drafted")
    issue = _month(plan.get("issue"))
    publish_date = first_monday(issue.year, issue.month)
    if not isinstance(source_facts, list) or len(source_facts) < 3:
        raise ValueError("At least three sourced evidence claims are required")
    claims, domains = [], set()
    for fact in source_facts:
        if not isinstance(fact, dict):
            raise ValueError("Invalid source claim")
        claim, url, asof = fact.get("claim"), fact.get("url"), fact.get("as_of")
        if not isinstance(claim, str) or not 20 <= len(claim.strip()) <= 600:
            raise ValueError("Evidence claim is missing or invalid")
        if not isinstance(url, str):
            raise ValueError("Evidence URL missing")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Evidence must have an HTTPS source URL")
        try:
            observed = date.fromisoformat(asof)
        except (TypeError, ValueError) as exc:
            raise ValueError("Evidence requires YYYY-MM-DD observation date") from exc
        if observed > publish_date or (publish_date - observed).days > 90:
            raise ValueError("Future or stale evidence date")
        domains.add(parsed.hostname.lower())
        claims.append(f"- {claim.strip()} (as of {asof}; source: {url})")
    if len(domains) < 2:
        raise ValueError("Use at least two independent source domains")

    body = (
        f"MSK ADVANCED STRATEGY LAB — DRAFT ONLY\n"
        f"Issue: {plan['issue']} | Volume {plan['volume']}\n"
        f"Research topic: {plan['title']}\n"
        f"Approach: {plan['angle']} | Editorial format: {plan['format']}\n\n"
        "EVIDENCE TO INDEPENDENTLY VERIFY\n"
        + "\n".join(claims)
        + "\n\nREVIEW REQUIREMENTS\n"
        "Provide a real source-dated example, limitations, invalidation rules, "
        "transaction costs where relevant, and what could cause losses. "
        "Do not infer win rate from indicators, and never portray estimated volume "
        "delta as trade-level delta. Source availability is not proof of correctness.\n"
        "STATUS: UNAPPROVED — NOT FOR AUTOMATIC PUBLICATION."
    )
    digest = sha256(body.encode("utf-8")).hexdigest()
    for old in published:
        if old.get("content_sha256") == digest:
            raise ValueError("Exact duplicate article draft")
        previous = old.get("article_body")
        if isinstance(previous, str) and SequenceMatcher(
            None, previous, body, autojunk=False
        ).ratio() >= 0.88:
            raise ValueError("Near-duplicate article draft requires new research")
    return {"issue": plan["issue"], "article_key": plan["article_key"],
            "body": body, "content_sha256": digest,
            "approved": False, "status": "DRAFT_REQUIRES_INDEPENDENT_REVIEW"}
