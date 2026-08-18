"""
report_generator.py — /report command PDF engine
────────────────────────────────────────────────────────────────────────────
Builds a professional, multi-page equity research–style PDF combining:
  • Cover / snapshot block (price, change, 52W range)
  • Technical Analysis  (RSI, MACD, EMA/SMA, ADX, ATR, Bollinger, ASI,
    trend read, support/resistance, target & stop-loss, price chart)
  • Fundamental Analysis (valuation, profitability, leverage, dividend)
  • Peer Comparison (3-5 same-sector stocks, ranked by market cap)
  • Long-Term Investment View (quality/valuation vs peers, 1-3yr+ horizon)
  • News digest
  • AI outlook (reuses ai_engine.ai_insights) — short-term/technical
  • Disclaimer footer + page numbers on every page

Design goal: reuse the SAME data sources as build_adv() in main.py so the
PDF numbers always match what the bot says in chat — no duplicated logic
for fetching data, only for presentation.

Public entrypoint:
    generate_stock_report_pdf(symbol_or_query: str) -> (ok, path_or_error, name)
"""

import os
import re
import logging
import html as _html
from datetime import datetime
from typing import Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, wait

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, HRFlowable, KeepTogether,
)
from reportlab.pdfgen import canvas as rl_canvas

from technical_indicators import (
    calc_rsi, calc_ema, calc_sma, calc_macd, calc_atr, calc_adx,
    calc_bollinger, calc_asi, rsi_label, trend_label, swing_signal,
)

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join("output", "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Brand palette ─────────────────────────────────────────────────────────
NAVY_HEX, GREEN_HEX, RED_HEX, GREY_HEX = "#0B2545", "#1B8A5A", "#C62828", "#5A6472"

NAVY      = colors.HexColor(NAVY_HEX)
NAVY_LT   = colors.HexColor("#13315C")
ACCENT    = colors.HexColor("#1E88E5")
GREEN     = colors.HexColor(GREEN_HEX)
RED       = colors.HexColor(RED_HEX)
GREY      = colors.HexColor(GREY_HEX)
GREY_LT   = colors.HexColor("#F2F4F7")
BORDER    = colors.HexColor("#D6DBE3")


# ══════════════════════════════════════════════════════════════════════════
# Small formatting helpers (mirrors main.py's conventions)
# ══════════════════════════════════════════════════════════════════════════
def _fmt_mcap(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v <= 0:
            return "N/A"
        cr = v / 1e7
        if cr >= 100000:
            return f"Rs {cr / 100000:.2f}L Cr"
        if cr >= 1000:
            return f"Rs {cr / 1000:.2f}K Cr"
        return f"Rs {cr:.2f} Cr"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_revenue(rev, mcap=None):
    if rev is None:
        return "N/A"
    try:
        r = float(rev)
        if r <= 0:
            return "N/A"
        if mcap:
            m = float(mcap)
            if m > 0 and r > m * 5:
                return "N/A"
        return _fmt_mcap(r)
    except (TypeError, ValueError):
        return "N/A"


def _n(v, suffix="", dash="N/A"):
    if v is None or v == "N/A":
        return dash
    try:
        return f"{float(v):,.2f}{suffix}"
    except (TypeError, ValueError):
        return f"{v}{suffix}"


def _rupee(v, dash="N/A"):
    if v is None:
        return dash
    try:
        return f"Rs {float(v):,.2f}"
    except (TypeError, ValueError):
        return dash


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2B50\u2705\u26A0\uFE0F]+"
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text or "").strip()


def _fix_currency(text: str) -> str:
    """PDF fonts (Helvetica/WinAnsi) have no glyph for the rupee sign (₹) —
    it renders as a black box in the PDF. Replace with 'Rs ' before it
    reaches a Paragraph. (Telegram chat text is untouched — ₹ renders fine
    there; this only applies to text destined for the PDF.)"""
    return re.sub(r"₹\s*", "Rs ", text or "")


def _clean_news_line(line: str) -> str:
    """Strip leading emoji/markers and any stray HTML from a news line."""
    line = re.sub(r"<[^>]+>", "", line)
    line = _html.unescape(line)
    line = _strip_emoji(line)
    line = _fix_currency(line)
    line = line.lstrip("• ").strip()
    return line


def _clean_ai_text(text: str) -> str:
    """Convert the bot's Telegram-HTML-flavoured AI text into paragraph-safe text."""
    text = _html.unescape(text or "")
    text = re.sub(r"</?b>", "", text)
    text = _strip_emoji(text).strip()
    text = _fix_currency(text)
    return text


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:
        logger.warning(f"[Report] {fn.__name__} failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════
# Peer discovery — same-sector companies via the static SECTOR_STOCKS map
# ══════════════════════════════════════════════════════════════════════════
def _find_peers(sym: str, max_peers: int = 5, fetch_timeout: int = 10) -> tuple:
    """
    Returns (sector_name, [peer_dicts]) where each peer_dict has
    sym/name/mcap/pe/roe. Peers are fetched concurrently (best-effort —
    a slow/failed peer is simply dropped, never blocks the report).
    """
    try:
        from nifty500_collector import get_stock_sector, SECTOR_STOCKS
        from fundamentals import get_fundamentals
    except Exception as e:
        logger.info(f"[Report] peer lookup unavailable: {e}")
        return None, []

    sector = get_stock_sector(sym)
    if not sector or sector == "📦 Others":
        return sector, []

    candidates = [s for s in SECTOR_STOCKS.get(sector, []) if s != sym][: max_peers + 3]
    if not candidates:
        return sector, []

    peers = []
    pool = ThreadPoolExecutor(max_workers=min(6, len(candidates)))
    try:
        futures = {pool.submit(_safe, get_fundamentals, c): c for c in candidates}
        done, not_done = wait(futures.keys(), timeout=fetch_timeout)
        for fut in done:
            c = futures[fut]
            try:
                f = fut.result()
            except Exception:
                f = None
            if not f or not f.get("mcap"):
                continue
            peers.append(dict(
                sym=c, name=f.get("name") or c, mcap=f.get("mcap"),
                pe=f.get("pe"), roe=f.get("roe"),
            ))
        for fut in not_done:
            fut.cancel()
    finally:
        pool.shutdown(wait=False)

    peers.sort(key=lambda p: p["mcap"] or 0, reverse=True)
    return sector, peers[:max_peers]


def _sector_avg(peers: list, key: str):
    vals = [p[key] for p in peers if isinstance(p.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


# ══════════════════════════════════════════════════════════════════════════
# Data assembly — reuses the exact same modules as build_adv()
# ══════════════════════════════════════════════════════════════════════════
def _collect_report_data(sym: str) -> dict:
    from data_engine import get_hist, get_info
    from fundamentals import get_fundamentals
    from market_news import get_stock_news
    from ai_engine import ai_insights as engine_ai_insights, long_term_view as engine_long_term_view

    sym = sym.upper().replace(".NS", "").replace(".BO", "")
    df = get_hist(sym, "1y")
    if df is None or df.empty or len(df) < 2:
        raise ValueError(f"No historical data available for {sym}.")

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = float(close.iloc[-2])
    chg = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
    day_hi = round(float(df["High"].iloc[-1]), 2)
    day_lo = round(float(df["Low"].iloc[-1]), 2)

    rsi = calc_rsi(close)
    macd, macd_sig, macd_hist = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    ema200 = calc_ema(close, 200) if len(close) >= 200 else None
    sma20 = calc_sma(close, 20)
    atr = calc_atr(df)
    adx, plus_di, minus_di = calc_adx(df)
    bb_mid, bb_up, bb_lo = calc_bollinger(close)
    asi = calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    r_label = rsi_label(rsi)
    signal = swing_signal(rsi, trend, chg)

    n = min(252, len(close))
    w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    w52l = round(float(close.rolling(n).min().iloc[-1]), 2)

    # ── Risk badge (annualized volatility) ──────────────────────────────
    # Reuses the 1y close series already fetched above — no extra API call.
    daily_ret = close.pct_change().dropna()
    ann_vol = round(float(daily_ret.std()) * (252 ** 0.5) * 100, 1) if len(daily_ret) > 20 else None
    if ann_vol is None:
        risk_level = "N/A"
    elif ann_vol < 25:
        risk_level = "Low"
    elif ann_vol < 40:
        risk_level = "Moderate"
    else:
        risk_level = "High"

    # ── Market context — stock's 1yr return vs Nifty 50 ─────────────────
    # Best-effort single extra fetch, reused for both the Technical section
    # context line and (further below) the Long-Term View's benchmark line.
    nifty_1y_ret = stock_1y_ret = None
    try:
        import yfinance as _yf
        _nifty_df = _yf.Ticker("^NSEI").history(period="1y")
        if _nifty_df is not None and len(_nifty_df) > 20:
            nifty_1y_ret = round(
                (float(_nifty_df["Close"].iloc[-1]) - float(_nifty_df["Close"].iloc[0]))
                / float(_nifty_df["Close"].iloc[0]) * 100, 1)
        if len(close) > 20:
            stock_1y_ret = round((ltp - float(close.iloc[0])) / float(close.iloc[0]) * 100, 1)
    except Exception as e:
        logger.info(f"[Report] Nifty benchmark unavailable: {e}")

    fund = _safe(get_fundamentals, sym) or {}
    info = _safe(get_info, sym) or {}

    name = fund.get("name") or info.get("name") or sym
    mcap = fund.get("mcap") or info.get("market_cap")
    rev = fund.get("rev") or info.get("totalRevenue")
    w52h = fund.get("w52h") or w52h
    w52l = fund.get("w52l") or w52l

    news_raw = _safe(get_stock_news, sym, 4) or ""
    news_lines = [_clean_news_line(l) for l in news_raw.split("\n") if l.strip()]

    # Target / stop-loss (same ATR logic used in chat card) — compute the
    # RAW numbers first, then feed the *same* numbers into the AI prompt so
    # the AI-Generated Outlook text can never quote a different target/SL
    # than the table above it.
    tgt_line = None
    sl_val = t1_val = 0.0
    if atr and atr > 0 and ltp > 0:
        if trend == "BULLISH":
            t1_val = round(ltp + 1.5 * atr, 2)
            sl_val = round(ltp - 2 * atr, 2)
            tgt_line = ("Target", f"Rs {t1_val:,.2f}  (+{(t1_val-ltp)/ltp*100:.1f}%)",
                        "Stop-loss", f"Rs {sl_val:,.2f}  ({(sl_val-ltp)/ltp*100:.1f}%)")
        elif trend == "BEARISH":
            t1_val = round(ltp - 1.5 * atr, 2)
            sl_val = round(ltp + 2 * atr, 2)
            tgt_line = ("Target", f"Rs {t1_val:,.2f}  ({(t1_val-ltp)/ltp*100:.1f}%)",
                        "Stop-loss", f"Rs {sl_val:,.2f}  (+{(sl_val-ltp)/ltp*100:.1f}%)")
        else:
            t1_val = round(ltp + atr, 2)          # R1 — used as the AI's reference "T1"
            sl_val = round(ltp - 2 * atr, 2)       # Range SL
            tgt_line = ("Resistance (R1)", f"Rs {t1_val:,.2f}",
                        "Support (S1)", f"Rs {round(ltp - atr, 2):,.2f}")

    ai_text = _safe(
        engine_ai_insights, sym, ltp, rsi, macd, trend,
        str(fund.get("pe") if fund.get("pe") is not None else "N/A"),
        str(fund.get("roe") if fund.get("roe") is not None else "N/A"),
        atr, sl_val, t1_val,
    ) or ""

    # Peers (same sector, ranked by market cap) — best-effort, never blocks
    sector, peers = _safe(_find_peers, sym) or (None, [])
    peer_avg_pe = _sector_avg(peers, "pe")
    peer_avg_roe = _sector_avg(peers, "roe")

    # Long-term investment view — separate AI call, fundamentals/quality
    # framed, no short-term price targets. Falls back to "" (section
    # skipped in the PDF) if AI is unavailable, never blocks report gen.
    long_term_text = ""
    _ema_for_lt = ema200 or ema50
    if _ema_for_lt:
        long_term_text = _safe(
            engine_long_term_view, sym, sector or "General", ltp,
            fund.get("pe"), fund.get("roe"), fund.get("de"), fund.get("div_y"),
            _ema_for_lt, w52h, w52l, peer_avg_pe, peer_avg_roe,
        ) or ""

    # Chart image (best-effort — report still generates without it)
    chart_path = None
    try:
        from chart_integration import get_chart_generator
        gen = get_chart_generator()
        ok, _meta, path = gen.generate(f"{sym}.NS", name, "6mo")
        if ok and path and os.path.exists(path):
            chart_path = path
    except Exception as e:
        logger.info(f"[Report] chart unavailable for {sym}: {e}")

    # Data-completeness — the fields an investor actually reads a fundamentals
    # section for. Counted explicitly rather than inferred from the whole dict
    # so a missing chart or news item (cosmetic) doesn't get conflated with a
    # missing PE or EPS (substantive).
    _core_fund_fields = {
        "PE": fund.get("pe"), "Forward PE": fund.get("fwd_pe"), "P/B": fund.get("pb"),
        "ROE": fund.get("roe"), "EPS": fund.get("eps"), "Revenue": rev,
        "D/E": fund.get("de"), "Dividend Yield": fund.get("div_y"), "Beta": fund.get("beta"),
    }
    fund_available = sum(1 for v in _core_fund_fields.values() if v is not None)
    fund_total = len(_core_fund_fields)

    return dict(
        sym=sym, name=name, ltp=ltp, chg=chg, day_hi=day_hi, day_lo=day_lo,
        rsi=rsi, rsi_label=r_label, macd=macd, macd_sig=macd_sig, macd_hist=macd_hist,
        ema20=ema20, ema50=ema50, ema200=ema200, sma20=sma20,
        atr=atr, adx=adx, plus_di=plus_di, minus_di=minus_di,
        bb_mid=bb_mid, bb_up=bb_up, bb_lo=bb_lo, asi=asi,
        trend=trend, signal=signal, w52h=w52h, w52l=w52l,
        mcap=mcap, rev=rev,
        pe=fund.get("pe"), fwd_pe=fund.get("fwd_pe"), pb=fund.get("pb"),
        roe=fund.get("roe"), eps=fund.get("eps"), de=fund.get("de"),
        div_y=fund.get("div_y"), beta=fund.get("beta"),
        sector=sector, peers=peers, peer_avg_pe=peer_avg_pe, peer_avg_roe=peer_avg_roe,
        long_term_text=long_term_text,
        news_lines=news_lines, ai_text=ai_text, tgt_line=tgt_line,
        chart_path=chart_path,
        risk_level=risk_level, ann_vol=ann_vol,
        stock_1y_ret=stock_1y_ret, nifty_1y_ret=nifty_1y_ret,
        fund_available=fund_available, fund_total=fund_total,
        generated_at=datetime.now().strftime("%d-%b-%Y %H:%M IST"),
    )


# ══════════════════════════════════════════════════════════════════════════
# PDF building
# ══════════════════════════════════════════════════════════════════════════
def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("ReportTitle", parent=ss["Heading1"], fontSize=20,
                           textColor=colors.white, leading=24, spaceAfter=0))
    ss.add(ParagraphStyle("ReportSub", parent=ss["Normal"], fontSize=10.5,
                           textColor=colors.HexColor("#C6D4E8"), leading=14))
    ss.add(ParagraphStyle("SectionHead", parent=ss["Heading2"], fontSize=13,
                           textColor=NAVY, spaceBefore=14, spaceAfter=6,
                           borderPadding=0))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.7,
                           leading=14, textColor=colors.HexColor("#20242C")))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8.3,
                           leading=11.5, textColor=GREY))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], fontSize=7,
                           leading=9.5, textColor=GREY, alignment=TA_CENTER))
    ss.add(ParagraphStyle("BigStat", parent=ss["Normal"], fontSize=17,
                           leading=20, textColor=NAVY))
    ss.add(ParagraphStyle("BigStatLabel", parent=ss["Normal"], fontSize=8,
                           leading=10, textColor=GREY))
    return ss


def _kv_table(rows, col_widths=(37*mm, 50*mm, 37*mm, 50*mm), header=None):
    """2x2-per-row key/value grid used for indicator + fundamentals blocks.
    Values are wrapped in Paragraphs so long strings (e.g. ADX + DI/-DI) wrap
    within their own column instead of overflowing into the next cell."""
    label_style = ParagraphStyle("kvLabel", fontName="Helvetica", fontSize=9,
                                  leading=11, textColor=GREY)
    val_style = ParagraphStyle("kvVal", fontName="Helvetica-Bold", fontSize=9,
                                leading=11, textColor=colors.HexColor("#1A1F27"))

    def _cell(text, is_val):
        return Paragraph(str(text), val_style if is_val else label_style)

    data = []
    if header:
        data.append(header)
    for row in rows:
        data.append([_cell(row[0], False), _cell(row[1], True),
                     _cell(row[2], False), _cell(row[3], True)])
    t = Table(data, colWidths=list(col_widths))
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, GREY_LT]),
    ]
    t.setStyle(TableStyle(style))
    return t


def _header_footer(canvas: rl_canvas.Canvas, doc, meta: dict):
    canvas.saveState()
    w, h = A4
    # top brand strip
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 14, w, 14, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(18 * mm, h - 10.5, "KSV / AutoAI Advisory — Equity Research Report")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 18 * mm, h - 10.5, meta.get("sym", ""))
    # footer
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(18 * mm, 10 * mm,
                       f"Generated {meta.get('generated_at','')} · Educational use only")
    canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.line(18 * mm, 13 * mm, w - 18 * mm, 13 * mm)
    canvas.restoreState()


def build_report_pdf(d: dict, output_path: str) -> str:
    """Pure presentation layer — takes the data dict from _collect_report_data
    (or an equivalent hand-built dict) and writes the PDF to output_path."""
    ss = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=22 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
        title=f"{d['sym']} Equity Research Report",
    )
    story = []

    chg_hex = GREEN_HEX if d["chg"] >= 0 else RED_HEX
    trend_color = GREEN if d["trend"] == "BULLISH" else RED if d["trend"] == "BEARISH" else GREY
    trend_hex = GREEN_HEX if d["trend"] == "BULLISH" else RED_HEX if d["trend"] == "BEARISH" else GREY_HEX

    # ── Cover banner ──────────────────────────────────────────────────
    banner_data = [[
        Paragraph(f"{d['name']}", ss["ReportTitle"]),
        Paragraph(f"<font color='white'>Rs {d['ltp']:,.2f}</font>", ss["ReportTitle"]),
    ], [
        Paragraph(f"NSE: {d['sym']}  ·  Technical &amp; Fundamental Analysis", ss["ReportSub"]),
        Paragraph(f"<font color='{chg_hex}'>"
                   f"{'+' if d['chg']>=0 else ''}{d['chg']:.2f}% today</font>", ss["ReportSub"]),
    ]]
    banner = Table(banner_data, colWidths=[110*mm, 62*mm])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("SPAN", (0, 0), (0, 0)),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 14),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(banner)
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"Report generated {d['generated_at']}", ss["Small"]))
    story.append(Spacer(1, 10))

    # ── Snapshot strip ────────────────────────────────────────────────
    def stat(label, val, color_hex=NAVY_HEX):
        return [Paragraph(f"<font color='{color_hex}'>{val}</font>", ss["BigStat"]),
                Paragraph(label, ss["BigStatLabel"])]

    risk_hex = {"Low": GREEN_HEX, "Moderate": "#B8860B", "High": RED_HEX}.get(d.get("risk_level"), GREY_HEX)
    risk_val = d.get("risk_level", "N/A")
    if d.get("ann_vol") is not None:
        risk_val = f"{risk_val} ({d['ann_vol']}%)"

    snap_cells = [
        stat("DAY RANGE", f"{_rupee(d['day_lo'])} – {_rupee(d['day_hi'])}"),
        stat("52-WEEK RANGE", f"{_rupee(d['w52l'])} – {_rupee(d['w52h'])}"),
        stat("TREND", d["trend"], trend_hex),
        stat("SIGNAL", _strip_emoji(d["signal"].split("—")[0]) if "—" in d["signal"] else _strip_emoji(d["signal"])),
    ]
    snap_table = Table([[c for pair in snap_cells for c in [pair[0]]],
                         [c for pair in snap_cells for c in [pair[1]]]],
                        colWidths=[43*mm]*4)
    snap_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREY_LT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ]))
    story.append(snap_table)

    # Risk badge — separate strip so it reads as a distinct "how volatile is
    # this" signal rather than getting lost among the four stats above.
    risk_row = stat("RISK LEVEL (ann. volatility)", risk_val, risk_hex)
    risk_table = Table([[risk_row[0]], [risk_row[1]]], colWidths=[174*mm])
    risk_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 4))
    story.append(risk_table)

    # ── Technical Analysis ────────────────────────────────────────────
    story.append(Paragraph("Technical Analysis", ss["SectionHead"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))

    tech_rows = [
        ["RSI (14)", f"{d['rsi']} — {d['rsi_label']}", "MACD", f"{d['macd']} (sig {d['macd_sig']}, hist {d['macd_hist']})"],
        ["EMA 20", _rupee(d["ema20"]), "EMA 50", _rupee(d["ema50"])],
        ["EMA 200", _rupee(d["ema200"]), "SMA 20", _rupee(d["sma20"])],
        ["ADX (14)", f"{_n(d['adx'])}  (+DI {_n(d['plus_di'])} / -DI {_n(d['minus_di'])})",
         "ATR (14)", _rupee(d["atr"])],
        ["Bollinger Mid", _rupee(d["bb_mid"]), "Bollinger Band", f"{_rupee(d['bb_lo'])} – {_rupee(d['bb_up'])}"],
        ["ASI (Accum. Swing)", _n(d["asi"]), "Overall Trend", d["trend"]],
    ]
    story.append(_kv_table(tech_rows))
    story.append(Spacer(1, 6))

    if d.get("tgt_line"):
        l1, v1, l2, v2 = d["tgt_line"]
        story.append(_kv_table([[l1, v1, l2, v2]]))
        story.append(Spacer(1, 6))

    tech_note = (
        f"Price is trading {'above' if d['ltp'] > d['ema20'] else 'below'} its 20-day EMA and "
        f"{'above' if d['ltp'] > d['ema50'] else 'below'} its 50-day EMA, consistent with the "
        f"<b>{d['trend']}</b> read. RSI of {d['rsi']} places the stock in the "
        f"<b>{d['rsi_label']}</b> zone. "
        f"{'ADX above 25 indicates a trending market.' if d['adx'] and d['adx'] > 25 else 'ADX below 25 suggests a range-bound / weak-trend market.' if d['adx'] else ''}"
    )
    story.append(Paragraph(tech_note, ss["Body"]))
    story.append(Spacer(1, 6))

    if d.get("stock_1y_ret") is not None and d.get("nifty_1y_ret") is not None:
        outperf = d["stock_1y_ret"] - d["nifty_1y_ret"]
        outperf_hex = GREEN_HEX if outperf >= 0 else RED_HEX
        story.append(Paragraph(
            f"<b>Market context:</b> {d['sym']} is {'+' if d['stock_1y_ret']>=0 else ''}{d['stock_1y_ret']}% "
            f"over the last year vs the Nifty 50's {'+' if d['nifty_1y_ret']>=0 else ''}{d['nifty_1y_ret']}% — "
            f"<font color='{outperf_hex}'>{'outperforming' if outperf >= 0 else 'underperforming'} "
            f"the broader market by {abs(round(outperf, 1))} pts</font>.",
            ss["Body"]))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 4))

    if d.get("chart_path"):
        try:
            img = RLImage(d["chart_path"], width=174*mm, height=95*mm, kind="proportional")
            story.append(img)
            story.append(Spacer(1, 4))
            story.append(Paragraph("6-month price chart with volume &amp; moving averages.", ss["Small"]))
        except Exception as e:
            logger.warning(f"[Report] chart embed failed: {e}")

    # ── Fundamental Analysis ──────────────────────────────────────────
    story.append(Paragraph("Fundamental Analysis", ss["SectionHead"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))

    fund_rows = [
        ["Market Cap", _fmt_mcap(d["mcap"]), "Revenue (TTM)", _fmt_revenue(d["rev"], d["mcap"])],
        ["PE (TTM)", _n(d["pe"]), "Forward PE", _n(d["fwd_pe"])],
        ["Price / Book", _n(d["pb"]), "EPS", _rupee(d["eps"])],
        ["ROE", _n(d["roe"], "%"), "Debt / Equity", _n(d["de"])],
        ["Dividend Yield", _n(d["div_y"], "%"), "Beta", _n(d["beta"])],
    ]
    story.append(_kv_table(fund_rows))
    story.append(Spacer(1, 8))

    # ── Peer Comparison ──────────────────────────────────────────────────
    if d.get("peers"):
        sector_clean = _strip_emoji(d.get("sector") or "").strip()
        story.append(Paragraph(f"Peer Comparison — {sector_clean}", ss["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))
        story.append(Paragraph(
            f"{d['name']} compared against the {len(d['peers'])} largest same-sector peers by market cap.",
            ss["Body"]))
        story.append(Spacer(1, 4))

        label_style = ParagraphStyle("peerLabel", fontName="Helvetica", fontSize=8.7,
                                      leading=11, textColor=colors.HexColor("#1A1F27"))
        val_style = ParagraphStyle("peerVal", fontName="Helvetica-Bold", fontSize=8.7,
                                    leading=11, textColor=colors.HexColor("#1A1F27"))
        self_style = ParagraphStyle("peerSelf", fontName="Helvetica-Bold", fontSize=8.7,
                                     leading=11, textColor=NAVY)

        header = [Paragraph(h, ParagraphStyle("peerHead", fontName="Helvetica-Bold", fontSize=8.7,
                                               textColor=colors.white))
                  for h in ["Company", "Symbol", "Market Cap", "PE", "ROE"]]
        rows_data = [header]
        rows_data.append([
            Paragraph(d["name"], self_style), Paragraph(f"{d['sym']} (this stock)", self_style),
            Paragraph(_fmt_mcap(d["mcap"]), self_style), Paragraph(_n(d["pe"]), self_style),
            Paragraph(_n(d["roe"], "%"), self_style),
        ])
        for p in d["peers"]:
            rows_data.append([
                Paragraph(p["name"], label_style), Paragraph(p["sym"], label_style),
                Paragraph(_fmt_mcap(p["mcap"]), val_style), Paragraph(_n(p.get("pe")), val_style),
                Paragraph(_n(p.get("roe"), "%"), val_style),
            ])
        peer_table = Table(rows_data, colWidths=[48*mm, 30*mm, 34*mm, 24*mm, 24*mm])
        peer_style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#E4ECF7")),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 2), (-1, -1), [colors.white, GREY_LT]),
        ]
        peer_table.setStyle(TableStyle(peer_style))
        story.append(peer_table)
        if d.get("peer_avg_pe") or d.get("peer_avg_roe"):
            avg_bits = []
            if d.get("peer_avg_pe"):
                avg_bits.append(f"peer avg PE {d['peer_avg_pe']:.1f}")
            if d.get("peer_avg_roe"):
                avg_bits.append(f"peer avg ROE {d['peer_avg_roe']:.1f}%")
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"Sector reference: {' · '.join(avg_bits)}.", ss["Small"]))
        story.append(Spacer(1, 8))

    # ── Long-Term Investment View ─────────────────────────────────────────
    if d.get("long_term_text"):
        story.append(Paragraph("Long-Term Investment View (1-3yr+)", ss["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))
        lt_clean = _clean_ai_text(d["long_term_text"])
        for line in lt_clean.split("\n"):
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                label, _, rest = line.partition(":")
                story.append(Paragraph(f"<b>{label.strip()}:</b>{rest}", ss["Body"]))
            else:
                story.append(Paragraph(line, ss["Body"]))
        story.append(Spacer(1, 4))
        bench_bits = []
        if d.get("stock_1y_ret") is not None and d.get("nifty_1y_ret") is not None:
            bench_bits.append(
                f"1-year return: {d['sym']} {'+' if d['stock_1y_ret']>=0 else ''}{d['stock_1y_ret']}% "
                f"vs Nifty 50 {'+' if d['nifty_1y_ret']>=0 else ''}{d['nifty_1y_ret']}%.")
        bench_bits.append(
            "This view is fundamentals-driven and separate from the short-term technical read "
            "above — it does not imply a near-term price target.")
        story.append(Paragraph(" ".join(bench_bits), ss["Small"]))
        story.append(Spacer(1, 8))

    # ── News ───────────────────────────────────────────────────────────
    if d.get("news_lines"):
        story.append(Paragraph("Recent News", ss["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))
        for line in d["news_lines"][:5]:
            story.append(Paragraph(f"•  {line}", ss["Body"]))
        story.append(Spacer(1, 8))

    # ── AI Outlook ─────────────────────────────────────────────────────
    if d.get("ai_text"):
        story.append(Paragraph("AI-Generated Outlook", ss["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=8))
        ai_clean = _clean_ai_text(d["ai_text"])
        for line in ai_clean.split("\n"):
            line = line.strip()
            if not line:
                continue
            story.append(Paragraph(line, ss["Body"]))
        story.append(Spacer(1, 8))

    # ── Data completeness & freshness ────────────────────────────────────
    story.append(Spacer(1, 4))
    fund_pct = round(d["fund_available"] / d["fund_total"] * 100) if d.get("fund_total") else None
    completeness_hex = GREEN_HEX if (fund_pct or 0) >= 80 else ("#B8860B" if (fund_pct or 0) >= 50 else RED_HEX)
    story.append(Paragraph(
        f"<b>Data completeness:</b> "
        f"<font color='{completeness_hex}'>{d.get('fund_available','?')}/{d.get('fund_total','?')} "
        f"fundamental fields available</font> from live sources"
        + (f" ({fund_pct}%)." if fund_pct is not None else ".")
        + " Price and technical indicators reflect the latest close; fundamental data may be cached "
          "for up to 4 hours for performance.",
        ss["Small"]))

    # ── Disclaimer ─────────────────────────────────────────────────────
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        "Disclaimer: This report is generated automatically from public market data (Yahoo "
        "Finance / NSE / Screener.in) and AI commentary for educational purposes only. It is "
        "not investment advice and the author is not a SEBI-registered research analyst. "
        "Past performance and technical signals do not guarantee future results. Please "
        "consult a qualified financial advisor before making investment decisions.",
        ss["Disclaimer"]))

    doc.build(
        story,
        onFirstPage=lambda c, dd: _header_footer(c, dd, d),
        onLaterPages=lambda c, dd: _header_footer(c, dd, d),
    )
    return output_path


# ══════════════════════════════════════════════════════════════════════════
# Public entrypoint
# ══════════════════════════════════════════════════════════════════════════
def generate_stock_report_pdf(symbol: str) -> Tuple[bool, str, Optional[str]]:
    """
    Fetches all data for `symbol` and writes a PDF report.
    Returns (success, path_or_error_message, company_name).
    """
    sym = str(symbol).upper().replace(".NS", "").replace(".BO", "").strip()
    if not sym:
        return False, "No symbol provided.", None
    try:
        data = _collect_report_data(sym)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUTPUT_DIR, f"{sym}_Report_{ts}.pdf")
        build_report_pdf(data, out_path)
        return True, out_path, data["name"]
    except ValueError as e:
        return False, str(e), None
    except Exception as e:
        logger.error(f"[Report] generation failed for {sym}: {e}", exc_info=True)
        return False, f"Report generation failed: {e}", None
