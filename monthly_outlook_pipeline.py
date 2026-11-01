"""Generate unapproved Monthly Outlook drafts from completed MSK sector-report data."""
import argparse
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monthly_outlook import MONTH_RE, current_month

IST = ZoneInfo("Asia/Kolkata")
NOTICE = ("Educational market research only; not SEBI-registered investment advice. "
          "Relative sector scores are not probabilities or trade recommendations.")


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt(value):
    return f"{value:+.1f}%" if valid_number(value) else "unavailable"


def build_draft(analysis, month=None, now=None):
    """Fail closed on missing, stale, or insufficient coverage; never self-approve."""
    now = now or datetime.now(IST)
    now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    month = month or now.strftime("%Y-%m")
    if not MONTH_RE.fullmatch(month) or month != now.strftime("%Y-%m"):
        raise ValueError("Generate a draft for the current market month only")
    if not isinstance(analysis, dict):
        raise ValueError("No verified analysis")
    indices = analysis.get("idx") or {}
    for name in ("Nifty 50", "Nifty Bank"):
        m = indices.get(name)
        if not m or not valid_number(m.get("ltp")) or m["ltp"] <= 0:
            raise ValueError("Missing verified index close: " + name)
    try:
        asof = datetime.strptime(analysis["asof"], "%d-%b-%Y").date()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid completed-session date") from exc
    days_old = (now.date() - asof).days
    if not 0 <= days_old <= 7:
        raise ValueError("Outlook index observations are stale or future-dated")
    found, total = analysis.get("n_unique"), analysis.get("n_configured")
    if not isinstance(found, int) or not isinstance(total, int) or total < 1 or found / total < 0.60:
        raise ValueError("Fewer than 60% of configured stocks have verified observations")
    sectors = [(n, m) for n, m in (analysis.get("sec") or {}).items()
               if valid_number(m.get("score"))]
    if len(sectors) < 5:
        raise ValueError("Fewer than five verified sector observations")
    sectors.sort(key=lambda x: (-x[1]["score"], x[0]))
    top = sectors[:3]
    bottom = sectors[-3:][::-1]
    def index_line(name):
        m = indices[name]
        ema = m.get("ema50")
        return (f"{name}: close {m['ltp']:,.2f}, 1M {fmt(m.get('r1m'))}, "
                f"3M {fmt(m.get('r3m'))}, {m.get('trend','unrated')}, "
                f"EMA50 {ema:,.2f}" if valid_number(ema) else
                f"{name}: close {m['ltp']:,.2f}, {m.get('trend','unrated')}, EMA50 unavailable")
    def sector_line(pair):
        name, m = pair
        return f"{name}: score {m['score']:.0f}/100, 1M {fmt(m.get('r1m'))}, {m.get('trend','unrated')}"
    high, low = analysis.get("swing_high"), analysis.get("swing_low")
    if not valid_number(high) or not valid_number(low) or high < low or low <= 0:
        high = low = None
    scenario = (
        [f"Positive scenario: monitor a sustained daily close above the observed 20-session high {high:,.2f}.",
         f"Negative scenario: monitor a sustained daily close below the observed 20-session low {low:,.2f}."]
        if high is not None else ["Breakout/breakdown reference levels unavailable; no price thresholds issued."]
    )
    lines = [
        "OBSERVED CLOSES | " + asof.isoformat(),
        index_line("Nifty 50"), index_line("Nifty Bank"), "",
        "BREADTH",
        f"Constituents with same-session observations: {found}/{total} ({100*found/total:.1f}%).",
        f"Above 50DMA: {fmt(analysis.get('p50_all'))}. Above 200DMA: {fmt(analysis.get('p200_all'))}.", "",
        "HIGHEST-RANKED SECTORS (relative strength, not buy signals)",
        *map(sector_line, top), "",
        "LOWEST-RANKED SECTORS (relative weakness, not sell signals)",
        *map(sector_line, bottom), "",
        "CONDITIONAL SCENARIOS (not forecasts)", *scenario, "",
        "DATA QUALITY",
        (str(analysis.get("freshness_warning"))
         if analysis.get("freshness_warning") else
         "Weekday-date freshness warning not raised; exchange holiday calendar not independently checked."),
        "Source: existing MSK sector/index collectors; source-level provenance requires review.",
        "Macroeconomic events, company filings and live quote accuracy are not independently verified.",
    ]
    # Render only complete lines within Telegram message limits.
    summary = ""
    for line in lines:
        if len(summary) + len(line) + 1 > 2850:
            summary += "\n[Further details require review.]"
            break
        summary += ("\n" if summary else "") + line
    return {
        "month": month, "approved": False,
        "published_at": now.date().isoformat(), "as_of": asof.isoformat(),
        "title": f"MSK Monthly Market Outlook — {month}",
        "summary": summary, "risk_notice": NOTICE,
        "review": {
            "status": "DRAFT_REQUIRES_HUMAN_APPROVAL",
            "constituents_covered": found, "constituents_expected": total,
            "sector_indices_covered": len(sectors),
            "source": "MSK sector_report.collect / market_data.bulk_history",
            "source_level_provenance_verified": False,
            "freshness_warning": analysis.get("freshness_warning"),
        },
    }


def build_pdf(report, path):
    """Make a clearly marked draft PDF without publishing it."""
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

    styles = getSampleStyleSheet()
    story = [
        Paragraph(escape(report["title"]), styles["Title"]),
        Paragraph("DRAFT — NOT APPROVED FOR PUBLICATION", styles["Heading2"]),
        Paragraph("Data as of: " + escape(report["as_of"]), styles["Normal"]),
        Spacer(1, 12),
    ]
    for line in report["summary"].split("\n"):
        if not line:
            story.append(Spacer(1, 7))
        elif line.isupper() or line.startswith(("HIGHEST-RANKED", "LOWEST-RANKED",
                                                 "OBSERVED CLOSES", "CONDITIONAL SCENARIOS")):
            story.append(Paragraph(escape(line), styles["Heading3"]))
        else:
            story.append(Paragraph(escape(line), styles["BodyText"]))
            story.append(Spacer(1, 5))
    story += [PageBreak(),
              Paragraph("Review and risk disclosures", styles["Heading1"]),
              Paragraph("Before publication, a reviewer must validate as-of dates, "
                        "source provenance, coverage, breadth, scenarios and market holidays.",
                        styles["BodyText"]),
              Spacer(1, 12),
              Paragraph(escape(report["risk_notice"]), styles["BodyText"]),
              Paragraph("Publication status: NOT APPROVED", styles["Heading2"])]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return str(path)


def atomic_write_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                         dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def generate(output_dir, month=None):
    """Collect once and produce draft artifacts outside the approved archive."""
    import sector_report
    now = datetime.now(IST)
    month = month or current_month()
    records = sector_report.collect()
    analysis = sector_report.analyze(records, now=now)
    nifty = analysis.get("nifty_df")
    if nifty is not None and len(nifty) >= 20:
        try:
            analysis["swing_high"] = float(nifty["High"].tail(20).max())
            analysis["swing_low"] = float(nifty["Low"].tail(20).min())
        except (KeyError, TypeError, ValueError):
            pass
    report = build_draft(analysis, month, now)
    root = Path(output_dir)
    json_path, pdf_path = root / (month + ".json"), root / (month + ".pdf")
    if json_path.exists() or pdf_path.exists():
        raise FileExistsError("Draft already exists; refuse silent overwrite")
    build_pdf(report, pdf_path)
    atomic_write_json(report, json_path)
    return str(json_path), str(pdf_path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=current_month())
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    paths = generate(args.output_dir, args.month)
    print("Unapproved draft written for review:", *paths)


if __name__ == "__main__":
    main()
