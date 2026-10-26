"""One evidence-gated NSE fundamental PDF per trading session.

GitHub Actions runs this script; it NEVER sends without a durable claim and
explicit opt-in. This module itself requires no API key, paid LLM or network at import.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The list is deliberately liquid NSE-listed equities, NOT a recommendation.
WATCHLIST = (
    "RELIANCE", "TCS", "INFY", "ITC", "HINDUNILVR", "BHARTIARTL",
    "LT", "SUNPHARMA", "MARUTI", "TITAN", "ULTRACEMCO", "NTPC",
    "POWERGRID", "ONGC", "WIPRO", "ADANIPORTS", "DRREDDY",
    "ASIANPAINT", "NESTLEIND", "TECHM", "HCLTECH", "EICHERMOT",
    "HINDALCO", "TATASTEEL", "BAJFINANCE", "KOTAKBANK", "SBIN",
    "ICICIBANK", "HDFCBANK", "AXISBANK",
)
FIRST_DAY = date(2026, 10, 26)


def weekdays_since(day: date) -> int:
    if day < FIRST_DAY:
        raise ValueError("Daily PDF start date is not reached")
    return sum((FIRST_DAY.toordinal() + i - 1) % 7 < 5
               for i in range((day - FIRST_DAY).days + 1)) - 1


def candidates(day: date, shortlist: tuple[str, ...] = WATCHLIST, n: int = 4) -> list[str]:
    if day.weekday() >= 5:
        return []
    offset = weekdays_since(day) % len(shortlist)
    return [shortlist[(offset + i) % len(shortlist)] for i in range(min(n, len(shortlist)))]


def load_state(path: str) -> dict:
    f = Path(path)
    if not f.exists():
        raise ValueError("Durable GitHub state missing; publication disabled")
    state = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("days"), dict):
        raise ValueError("Invalid daily-research state format")
    return state


def save_state(path: str, state: dict) -> None:
    f = Path(path)
    f.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def claim(day: date, state: dict, metadata: dict) -> dict:
    if day.weekday() >= 5 or not candidates(day):
        raise ValueError("Not an NSE market weekday")
    k = day.isoformat()
    if k in state["days"]:
        raise ValueError("Daily slot already claimed; no repeat delivery")
    if metadata.get("day") != k or metadata.get("symbol") not in candidates(day):
        raise ValueError("Prepared report does not match scheduled stock/date")
    state["days"][k] = {"status": "CLAIMED", "symbol": metadata["symbol"],
                        "report_sha256": metadata["sha256"]}
    return state


def confirm(day: date, state: dict, message_id: int) -> dict:
    row = state["days"].get(day.isoformat())
    if not row or row["status"] != "CLAIMED":
        raise ValueError("Cannot confirm unclaimed slot")
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
        raise ValueError("Invalid Telegram acknowledgement")
    row.update(status="SENT", message_id=message_id)
    return state


def finite_positive(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def validate_inputs(inp: dict, today: date) -> list[str]:
    errors = []
    df = inp.get("df")
    if df is None or getattr(df, "empty", True) or len(df) < 90:
        return ["Price history has fewer than 90 sessions"]
    try:
        last = df.index[-1].date()
        # A daily post after NSE close must not present last week's candle as today's.
        if last != today:
            errors.append("Last confirmed closing candle not dated today")
        if not finite_positive(df["Close"].iloc[-1]):
            errors.append("Invalid latest closing price")
    except Exception:
        errors.append("No reliable closing date/price")
    fund = inp.get("fund") or {}
    for key in ("pe", "roe", "mcap"):
        if not finite_positive(fund.get(key)):
            errors.append(f"Missing or invalid {key} fundamental")
    stm = inp.get("stm") or {}
    years = stm.get("years") or []
    quarters = stm.get("quarters") or []
    if len(years) < 4:
        errors.append("Fewer than 4 annual financial periods")
    if len(quarters) < 4:
        errors.append("Fewer than 4 quarterly financial periods")
    if years:
        row = years[-1]
        for key in ("revenue", "net_profit"):
            if not finite_positive(row.get(key)):
                errors.append(f"Missing annual {key}")
        if not row.get("label"):
            errors.append("Annual statements have no as-of label")
    ownership = (inp.get("extras") or {}).get("shareholding") or []
    if len(ownership) < 3:
        errors.append("Ownership history is incomplete")
    return errors


def validate_pdf(path: str, R: dict) -> list[str]:
    """Require text-accessible PDF with actual fundamental sections, not just pages."""
    errors = []
    try:
        from pypdf import PdfReader
        p = Path(path)
        if p.stat().st_size < 15_000:
            errors.append("Generated PDF is too small")
        with p.open("rb") as f:
            if f.read(5) != b"%PDF-":
                errors.append("Generated file is not a PDF")
        reader = PdfReader(path)
        if len(reader.pages) < 4:
            errors.append("PDF has fewer than 4 pages")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for term in (R["sym"], "Fundamental Analysis", "Annual financials",
                     "Valuation", "Risk", "Educational"):
            if term.casefold() not in text.casefold():
                errors.append(f"PDF missing required research section: {term}")
    except Exception as exc:
        errors.append("PDF could not be validated: " + type(exc).__name__)
    return errors


def prepare(day: date, directory: str) -> dict:
    import hashlib
    import report_generator as rg
    if not candidates(day):
        raise ValueError("No weekday session")
    failures = []
    Path(directory).mkdir(parents=True, exist_ok=True)
    for sym in candidates(day):
        pdf = str(Path(directory) / f"{sym}_Daily_Fundamental_{day.isoformat()}.pdf")
        try:
            inp = rg.collect_inputs(sym)
            if inp.get("sym") != sym:
                raise ValueError("Collected company symbol mismatches selected symbol")
            problems = validate_inputs(inp, day)
            if problems:
                raise ValueError("; ".join(problems))
            R = rg.assemble(inp, use_ai=False)
            pillars = ((R.get("sc") or {}).get("pillars") or {})
            fs = [pillars.get(k) for k in ("Quality", "Valuation", "Growth")]
            strengths = [float(s) for s in fs if isinstance(s, (int, float)) and math.isfinite(s)]
            if (R["sc"].get("insufficient_evidence") or len(strengths) < 2 or
                    sum(strengths) / len(strengths) < 55):
                raise ValueError("Insufficient evidence or low fundamental strength")
            rg.build_pdf(R, pdf)
            errors = validate_pdf(pdf, R)
            if errors:
                raise ValueError("; ".join(errors))
            sha = hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
            meta = {"day": day.isoformat(), "symbol": sym, "company": R["name"],
                    "asof": R["asof"], "pdf": str(Path(pdf).resolve()),
                    "sha256": sha, "pages_verified": True}
            return meta
        except Exception as exc:
            failures.append(f"{sym}: {str(exc)[:130]}")
            try:
                Path(pdf).unlink(missing_ok=True)
            except OSError:
                pass
    raise ValueError("No complete, same-session research PDF: " + " | ".join(failures))


def send(meta: dict) -> int:
    """Called only AFTER the durable CLAIMED record was pushed to GitHub."""
    import hashlib
    import telebot
    token = os.getenv("TELEGRAM_TOKEN", "")
    target = os.getenv("MSK_DAILY_RESEARCH_CHAT_ID", "")
    if not token or not re.fullmatch(r"-?\d{5,20}", target):
        raise ValueError("Telegram bot token or target chat ID is not configured")
    p = Path(meta["pdf"])
    if hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
        raise ValueError("PDF changed between validation and claimed delivery")
    caption = (f"📄 MSK Daily Fundamental Research | {meta['symbol']}\n"
               f"{meta['company']}\n"
               f"Financial and price data reviewed as of {meta['asof']}.\n"
               "Full fundamental, peer, technical and risk details inside.\n"
               "Educational research, not SEBI-registered investment advice.")
    bot = telebot.TeleBot(token, threaded=False)
    with p.open("rb") as file:
        receipt = bot.send_document(target, file,
                                    visible_file_name=p.name,
                                    caption=caption, timeout=120)
    msg_id = getattr(receipt, "message_id", None)
    if not isinstance(msg_id, int) or msg_id <= 0:
        raise RuntimeError("No Telegram message ID; delivery ambiguous")
    return msg_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("check", "prepare", "claim", "send", "confirm"))
    ap.add_argument("--day", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--metadata", default="output/daily_fundamental.json")
    ap.add_argument("--output", default="output/daily")
    ap.add_argument("--message-id", type=int, default=0)
    a = ap.parse_args()
    today = date.fromisoformat(a.day)
    state = load_state(a.state)
    if a.action == "check":
        if today.weekday() >= 5:
            print("SKIP: weekend")
            return 2
        if a.day in state["days"]:
            print("SKIP: already claimed, never auto-retry")
            return 2
        if os.getenv("MSK_DAILY_RESEARCH_ENABLED") != "true" or not os.getenv("TELEGRAM_TOKEN") or not os.getenv("MSK_DAILY_RESEARCH_CHAT_ID"):
            print("PAUSED: opt-in or Telegram credentials unavailable")
            return 2
        print("READY: one evidence-gated PDF is permitted")
    elif a.action == "prepare":
        meta = prepare(today, a.output)
        p = Path(a.metadata)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, indent=2) + "\n")
        print("PDF validated:", meta["symbol"], meta["asof"])
    elif a.action == "claim":
        meta = json.loads(Path(a.metadata).read_text())
        save_state(a.state, claim(today, state, meta))
        print("CLAIMED: state must be persisted before Telegram send")
    elif a.action == "send":
        meta = json.loads(Path(a.metadata).read_text())
        row = state["days"].get(a.day, {})
        if row.get("status") != "CLAIMED" or row.get("report_sha256") != meta["sha256"]:
            raise ValueError("No durable matching claim for PDF")
        # stdout is captured by workflow and not logged; do not re-send on failure.
        print(send(meta))
    elif a.action == "confirm":
        save_state(a.state, confirm(today, state, a.message_id))
        print("SENT: Telegram message ID confirmed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("MSK DAILY PDF: " + str(exc)[:700], file=sys.stderr)
        sys.exit(1)
