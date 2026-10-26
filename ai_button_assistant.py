"""Optional per-button AI research Q&A and professionally drafted summaries.

The existing bot/buttons create deterministic market reports. This companion
keeps that original output intact, remembers a *bounded*, *short-lived* public
research excerpt for the requesting chat, and only calls LLMs after an explicit
button action. It never sends portfolio holdings, transactions or P&L to AI.

Safety:
- No persistent conversation/context storage; private chat only.
- Reports are market FEED observations, NOT exchange-verified/realtime facts.
- Reject entirely novel numerical claims; never synthesize targets, ratios,
  timestamps, headlines or issuer financial statements.
- No AI connection/response is promised if all endpoints fail.
"""
import html
import logging
import math
import re
import threading
import time

from ai_engine import _call_ai, ai_available
from research_engine import numbers_grounded

logger = logging.getLogger(__name__)
TOPICS = frozenset((
    "stock", "technical", "fundamental", "chart", "pdf", "conservative",
    "moderate", "aggressive", "breadth", "swing_safe", "swing_agr",
    "buzz", "sector", "portfolio", "general",
))
MAX_CONTEXT = 3100
MAX_QUESTION = 550
TTL_SECONDS = 15 * 60
MAX_STORE = 120
_LOCK = threading.Lock()
_CONTEXTS = {}

# Hardcoded or internal app data must not be treated as verified current news.
_UNSAFE_CONTEXT = re.compile(
    r"(?:\bportfolio\b.{0,40}\b(?:qty|holdings|p&l|avg:|₹)|"
    r"\b(?:api[_ -]?key|bearer|token|password)\b.{0,15}[:=])",
    re.I | re.S,
)
_NON_FACT = re.compile(r"(?:^|\n)\s*(?:\W|.{0,15})?(?:auto.generated|generic fallback)\b", re.I)
_TAGS = re.compile(r"<[^>]{0,250}>")


def clean_excerpt(value):
    """Treat Telegram HTML as a display format, not trusted evidence."""
    if not isinstance(value, str):
        return ""
    plain = html.unescape(_TAGS.sub("", value))
    plain = re.sub(r"\r\n?", "\n", plain)
    plain = plain.replace("\x00", "")
    lines = []
    for line in plain.splitlines():
        line = " ".join(line.split())
        if line and not _NON_FACT.search(line):
            lines.append(line[:450])
    return "\n".join(lines)[:MAX_CONTEXT]


def remember(chat_id, topic, evidence, *, now=None):
    """Bounded in-memory context; never store raw private positions."""
    if topic not in TOPICS or topic == "portfolio":
        return False
    text = clean_excerpt(evidence)
    if len(text) < 15 or _UNSAFE_CONTEXT.search(text):
        return False
    if now is None:
        now = time.monotonic()
    with _LOCK:
        for key, (ts, _) in list(_CONTEXTS.items()):
            if now - ts > TTL_SECONDS:
                _CONTEXTS.pop(key, None)
        if len(_CONTEXTS) >= MAX_STORE:
            oldest = min(_CONTEXTS, key=lambda k: _CONTEXTS[k][0])
            _CONTEXTS.pop(oldest, None)
        _CONTEXTS[(chat_id, topic)] = (now, text)
    return True


def recall(chat_id, topic, *, now=None):
    if now is None:
        now = time.monotonic()
    with _LOCK:
        entry = _CONTEXTS.get((chat_id, topic))
        if not entry:
            return None
        stamp, body = entry
        if now - stamp > TTL_SECONDS:
            _CONTEXTS.pop((chat_id, topic), None)
            return None
        return body


def _is_market_claim(message):
    return any(s in message.lower() for s in (
        "price", "ltp", "target", "buy", "sell", "nifty", "stock", "today",
        "returns", "ratio", "pe", "roe", "growth", "moving average",
        "fundamental", "technical", "dividend", "market", "current", "अब",
        "भाव", "आज", "खरीद", "बेच", "निफ्टी", "शेयर",
    ))


def _draft_prompt(topic, question, facts):
    privacy = (
        "Portfolio holdings/amounts are PRIVATE and not provided. Do not "
        "pretend you know the user's portfolio. Explain general risk concepts only."
        if topic == "portfolio" else ""
    )
    system = (
        "You are the MSK Indian equity research drafting assistant. "
        "Write a CLEAN concise research response to the user's question using "
        "only the supplied market context (which has not been independently verified). "
        "The context is historical/public feed output, NOT exchange-certified live quotes. "
        "Do not invent figures, trading targets, future returns, IPO dates, company "
        "statements, sources or catalysts. If no verifiable data, explicitly say "
        "'Data unavailable / not independently verified' and suggest how to verify. "
        "Structure into View, Evidence, Risks, Next checks when applicable. "
        "Speak in the user's language, no markdown tables or HTML. "
        "Any number used must exactly come from supplied context or question. "
        "No overconfident BUY/SELL call, no individual investment advice. "
        + privacy
    )
    prompt = (
        f"BUTTON: {topic}\n"
        f"RESEARCH CONTEXT (SOURCE AND AS-OF DATE MAY BE UNVERIFIED):\n"
        f"{facts or 'No independently dated market evidence available.'}\n"
        f"USER QUESTION:\n{question}"
    )
    return system, prompt


def draft(chat_id, topic, question, *, now=None, caller=None):
    """Returns (safe display text, used_llm_bool); callable without Telegram.

    Caller is injectable for tests; in production it is the shared AI gateway.
    """
    if topic not in TOPICS:
        return "Unsupported research button.", False
    if not isinstance(question, str):
        return "Please send a text question.", False
    question = " ".join(question.strip().split())[:MAX_QUESTION]
    if not question:
        return "Please type a question about the selected report.", False
    facts = recall(chat_id, topic, now=now)
    if topic == "portfolio":
        # Always safe-by-default. User explicitly asking is NOT permission
        # to transmit previously stored portfolio balances or transactions.
        facts = None
    if facts is None and topic not in ("general", "portfolio"):
        return ("The previous research data has expired or is unavailable. "
                "Please run this button again, then ask your question."), False
    educational = any(question.lower().startswith(prefix) for prefix in (
        "what is ", "what does ", "explain ", "define ", "how does ",
        "how can ", "difference between ", "meaning of ", "tell me about ",
        "क्या है", "समझाओ", "कैसे ", "મતલબ", "સમજાવો",
    ))
    if not facts and _is_market_claim(question) and not educational:
        return ("Current market data are not supplied. Please select a stock "
                "and run Technical/Fundamental/Report first."), False
    system, prompt = _draft_prompt(topic, question, facts)
    if caller is None:
        if not ai_available():
            return "AI service not configured. Original research remains available.", False
        caller = _call_ai
    try:
        response, _ = caller(
            [{"role": "user", "content": prompt}], 650, system)
    except Exception:
        logger.warning("AI button drafting failed: sanitized exception")
        return "AI drafting temporarily unavailable. Original research was not changed.", False
    if not isinstance(response, str) or not response.strip():
        return "AI drafting temporarily unavailable. Original research was not changed.", False
    body = response.strip()
    # Reject fabricated numbers, including wrong fiscal years or small ratios.
    # If question supplied a numeric request, it is part of the source facts,
    # not automatically a verified market observation.
    if not numbers_grounded(body, (facts or "") + "\n" + question, max_unmatched=0):
        logger.warning("AI button draft rejected: number not grounded in source")
        return ("AI draft rejected because it introduced a number not present "
                "in the selected research data. Please rely on the original report."), False
    if any(phrase in body.lower() for phrase in (
        "guaranteed return", "sure-shot profit", "latest news confirms",
        "insider sources", "company announced today",
    )):
        return "AI draft rejected due to an unsupported market claim.", False
    return body[:2500], True


def escape_telegram(text):
    """No AI-provided formatting controls; safe for HTML Telegram sends."""
    return html.escape(text, quote=False)
