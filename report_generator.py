"""
report_generator.py — Institutional-style equity research PDF  (v2)
────────────────────────────────────────────────────────────────────────────────
Three stages, so each can be tested on its own:

  collect_inputs(sym)   network: price history, Nifty, fundamentals, statements,
                        shareholding, peers, consensus, news   (parallelised)
  assemble(inputs)      research_engine + report_charts → one `R` dict
  build_pdf(R, path)    reportlab presentation only

Report structure
  1  Executive summary  — stance, composite score, horizons, strengths/risks
  2  Investment view    — analyst commentary, bull/bear, valuation cross-check
  3  Technical analysis — chart, multi-timeframe, returns vs Nifty, key levels
  4  Risk & relative performance
  5  Fundamental analysis — valuation, growth, margins, cash flow, 10-yr table
  6  Ownership & peers
  7  Scenarios & trade plan
  8  Risk matrix & catalysts
  9  Appendix — methodology, data sources, glossary, disclaimer

Sections whose data could not be retrieved are omitted (never a page of "N/A").

Public entrypoint (unchanged):
    generate_stock_report_pdf(symbol) -> (ok, path_or_error, company_name)
"""

import os
import re
import html as _html
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor, wait

import matplotlib
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
    HRFlowable, PageBreak, CondPageBreak, KeepTogether,
)

import research_engine as eng
import report_charts as charts
import industry as industry_mod
import peer_groups
import research_data as rd
from config import BRAND_NAME as BRAND

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join("output", "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════
# Fonts — DejaVu ships with matplotlib and has the ₹ glyph (Helvetica does not)
# ══════════════════════════════════════════════════════════════════════════
FONT, BOLD, OBLQ = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
HAS_RUPEE = False
try:
    _fd = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DV", os.path.join(_fd, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DV-B", os.path.join(_fd, "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DV-I", os.path.join(_fd, "DejaVuSans-Oblique.ttf")))
    pdfmetrics.registerFont(TTFont("DV-BI", os.path.join(_fd, "DejaVuSans-BoldOblique.ttf")))
    pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-BI")
    FONT, BOLD, OBLQ, HAS_RUPEE = "DV", "DV-B", "DV-I", True
    # Monospace has a serifed capital I and a curved-tail l, so "FIIs"/"DIIs" can never be misread as "Fils"/"Dlls".
    pdfmetrics.registerFont(TTFont("DVM", os.path.join(_fd, "DejaVuSansMono-Bold.ttf")))
except Exception as _e:                                       # pragma: no cover
    logger.warning(f"[Report] DejaVu fonts unavailable, falling back to Helvetica: {_e}")

# ── palette ──────────────────────────────────────────────────────────────
NAVY = colors.HexColor("#0B2545")
GREEN, RED = colors.HexColor("#1B8A5A"), colors.HexColor("#C62828")
GREY, GREY_LT = colors.HexColor("#5A6472"), colors.HexColor("#F2F4F7")
BORDER, INK = colors.HexColor("#D6DBE3"), colors.HexColor("#1A1F27")
GREEN_H, RED_H, AMBER_H, GREY_H, NAVY_H = "#1B8A5A", "#C62828", "#B8860B", "#5A6472", "#0B2545"

W = 178 * mm                                                  # usable width


# ══════════════════════════════════════════════════════════════════════════
# text helpers
# ══════════════════════════════════════════════════════════════════════════
def esc(s) -> str:
    return _html.escape(str(s), quote=False)


def holder(key: str) -> str:
    """Canonical, typographically unambiguous holder label (FIIs / DIIs always capital I)."""
    name = rd.holder_display(key)
    return f"<font name='DVM'>{name}</font>" if key in ("fii", "dii") and HAS_RUPEE else name


_HOLDER_RE = re.compile(r"(?<![>\w])(FIIs|DIIs)(?![\w<])")


def _rs(text: str) -> str:
    """Final text pass for every paragraph: ₹ fallback, and FIIs/DIIs in the serifed-I monospace face
    so a capital I can never be misread as a lowercase l ('Fils', 'Dlls')."""
    if HAS_RUPEE:
        return _HOLDER_RE.sub(lambda m: f"<font name='DVM'>{m.group(1)}</font>", text)
    return text.replace("₹", "Rs ")


_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2B50\u2705\u26A0\uFE0F]+")


def strip_emoji(s: str) -> str:
    return _EMOJI_RE.sub("", s or "").strip()


def n(v, nd=2, suf="", dash="—"):
    if v is None:
        return dash
    try:
        return f"{float(v):,.{nd}f}{suf}"
    except (TypeError, ValueError):
        return dash


def inr(v, nd=2, dash="—"):
    return dash if v is None else f"₹{float(v):,.{nd}f}"


def cr(v, dash="—"):
    if v is None:
        return dash
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.1f}"


def mcap_txt(v):
    try:
        v = float(v)
        if v <= 0:
            return "—"
        c = v / 1e7
        return f"₹{c / 100000:.2f} L Cr" if c >= 100000 else f"₹{c:,.0f} Cr"
    except (TypeError, ValueError):
        return "—"


def sgn_color(v, good_pos=True):
    if v is None:
        return GREY_H
    return GREEN_H if (v >= 0) == good_pos else RED_H


def colored(text, hexcol, bold=True):
    return f"<font color='{hexcol}'>{'<b>' if bold else ''}{text}{'</b>' if bold else ''}</font>"


# ══════════════════════════════════════════════════════════════════════════
# styles
# ══════════════════════════════════════════════════════════════════════════
def S():
    def ps(name, **kw):
        base = dict(fontName=FONT, fontSize=8.6, leading=12.4, textColor=INK)
        base.update(kw)
        return ParagraphStyle(name, **base)
    return {
        "body": ps("body"),
        "just": ps("just", alignment=4),
        "small": ps("small", fontSize=7.4, leading=10, textColor=GREY),
        "tiny": ps("tiny", fontSize=6.6, leading=9, textColor=GREY),
        "h1": ps("h1", fontName=BOLD, fontSize=12.5, leading=16, textColor=NAVY, spaceBefore=10, spaceAfter=2),
        "h2": ps("h2", fontName=BOLD, fontSize=9.4, leading=13, textColor=NAVY, spaceBefore=6, spaceAfter=2),
        "title": ps("title", fontName=BOLD, fontSize=19, leading=23, textColor=colors.white),
        "sub": ps("sub", fontSize=8.8, leading=12, textColor=colors.HexColor("#C6D4E8")),
        "brand": ps("brand", fontName=BOLD, fontSize=7.8, leading=10, textColor=colors.HexColor("#8FB4E3")),
        "px": ps("px", fontName=BOLD, fontSize=17, leading=21, textColor=colors.white, alignment=TA_RIGHT),
        "pxs": ps("pxs", fontSize=8.8, leading=12, textColor=colors.white, alignment=TA_RIGHT),
        "cell": ps("cell", fontSize=8, leading=10.6),
        "cellb": ps("cellb", fontName=BOLD, fontSize=8, leading=10.6),
        "cellr": ps("cellr", fontSize=8, leading=10.6, alignment=TA_RIGHT),
        "cellrb": ps("cellrb", fontName=BOLD, fontSize=8, leading=10.6, alignment=TA_RIGHT),
        "th": ps("th", fontName=BOLD, fontSize=7.8, leading=10, textColor=colors.white),
        "thr": ps("thr", fontName=BOLD, fontSize=7.8, leading=10, textColor=colors.white, alignment=TA_RIGHT),
        "lab": ps("lab", fontSize=7.4, leading=9.6, textColor=GREY),
        "big": ps("big", fontName=BOLD, fontSize=20, leading=24, textColor=NAVY),
        "stance": ps("stance", fontName=BOLD, fontSize=12, leading=15, textColor=NAVY),
        "bullet": ps("bullet", fontSize=8.4, leading=11.8, leftIndent=9, bulletIndent=0),
        "disc": ps("disc", fontSize=6.8, leading=9.2, textColor=GREY, alignment=TA_LEFT),
    }


ST = S()


def P(text, style="body"):
    return Paragraph(_rs(text), ST[style])


def img(path, width=W):
    try:
        iw, ih = ImageReader(path).getSize()
        return RLImage(path, width=width, height=width * ih / iw)
    except Exception as e:
        logger.warning(f"[Report] image embed failed: {e}")
        return None


def section(title, sub=None):
    out = [CondPageBreak(60 * mm), P(esc(title), "h1"),
           HRFlowable(width="100%", thickness=1.2, color=NAVY, spaceAfter=5)]
    if sub:
        out += [P(esc(sub), "small"), Spacer(1, 3)]
    return out


def table(data, widths, header=True, zebra=True, extra=None):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
          ("BOX", (0, 0), (-1, -1), 0.5, BORDER)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    if zebra:
        st += [("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, GREY_LT])]
    if extra:
        st += extra
    t.setStyle(TableStyle(st))
    return t


def kv(rows, widths=(38 * mm, 51 * mm, 38 * mm, 51 * mm)):
    data = [[P(a, "lab"), P(b, "cellb"), P(c, "lab"), P(d, "cellb")] for a, b, c, d in rows]
    return table(data, list(widths), header=False)


def callout(text, hexcol=NAVY_H, bg="#F2F6FC"):
    t = Table([[P(text)]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(hexcol)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def bullets(items: List[str], color_hex=None):
    out = []
    for it in items:
        mark = f"<font color='{color_hex}'>●</font>" if color_hex else "•"
        out.append(Paragraph(_rs(f"{mark}&nbsp;&nbsp;{it}"), ST["bullet"]))
    return out


# ══════════════════════════════════════════════════════════════════════════
# Canvas — header / footer / "Page x of y"
# ══════════════════════════════════════════════════════════════════════════
def make_canvas(meta: dict):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                self._chrome(total)
                super().showPage()
            super().save()

        def _chrome(self, total):
            w, h = A4
            self.saveState()
            self.setFillColor(NAVY)
            self.rect(0, h - 13, w, 13, fill=1, stroke=0)
            self.setFillColor(colors.white)
            self.setFont(BOLD, 7.6)
            self.drawString(16 * mm, h - 9, f"{BRAND}  |  {meta.get('kind', 'Equity Research')}")
            self.setFont(FONT, 7.6)
            self.drawRightString(w - 16 * mm, h - 9, meta.get("right") or f"{meta['name'][:40]}  ·  NSE: {meta['sym']}")
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.5)
            self.line(16 * mm, 13 * mm, w - 16 * mm, 13 * mm)
            self.setFillColor(GREY)
            self.setFont(FONT, 6.8)
            self.drawString(16 * mm, 9 * mm, f"{BRAND}  ·  Generated {meta['generated']}  ·  Automated research for educational use. Not SEBI-registered advice.")
            self.drawRightString(w - 16 * mm, 9 * mm, f"Page {self._pageNumber} of {total}")
            self.restoreState()
    return NumberedCanvas


# ══════════════════════════════════════════════════════════════════════════
# STAGE 1 — collect (network)
# ══════════════════════════════════════════════════════════════════════════
def _safe(fn, *a, default=None, **k):
    try:
        return fn(*a, **k)
    except Exception as e:
        logger.warning(f"[Report] {getattr(fn, '__name__', 'call')} failed: {str(e)[:120]}")
        return default


def _find_peers(sym: str, kind: Optional[str], mcap: Optional[float], sector_label: Optional[str],
                max_peers: int = 6, timeout: int = 25):
    """
    → (group label, peers, method). Resolution: curated lender groups (SBFC etc.) →
    lender kind + size → the bot's sector map. Peers are the `max_peers` closest in market cap.
    """
    from nifty500_collector import SECTOR_STOCKS
    from fundamentals import get_fundamentals
    from market_data import bulk_history
    label, cands, method = peer_groups.resolve(sym, kind, mcap)
    if not cands:
        if sector_label and "Others" not in sector_label and sector_label in SECTOR_STOCKS:
            label, cands, method = sector_label, [c for c in SECTOR_STOCKS[sector_label] if c != sym], "sector map"
        else:
            return None, [], "none"
    cands = cands[: max_peers + 6]
    peers = []
    pool = ThreadPoolExecutor(max_workers=min(6, max(1, len(cands))))
    try:
        futs = {pool.submit(_safe, get_fundamentals, c): c for c in cands}
        done, _ = wait(futs.keys(), timeout=timeout)
        for f in done:
            c, d = futs[f], f.result()
            if d and d.get("mcap"):
                peers.append({"sym": c, "name": d.get("name") or c, "mcap": d["mcap"], "pe": d.get("pe"),
                              "fwd_pe": d.get("fwd_pe"), "pb": d.get("pb"), "roe": d.get("roe")})
    finally:
        pool.shutdown(wait=False)
    if mcap:
        import math
        peers.sort(key=lambda p: abs(math.log((p["mcap"] or 1) / mcap)))
    else:
        peers.sort(key=lambda p: p["mcap"] or 0, reverse=True)
    peers = peers[:max_peers]
    if peers:
        hist = _safe(bulk_history, [p["sym"] for p in peers], "1y", default={}) or {}
        for p in peers:
            df = hist.get(p["sym"])
            p["ret_1y"] = (round((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[0]) - 1) * 100, 1)
                           if df is not None and len(df) > 100 else None)
    return label, peers, method


_QUOTE_PAGE_RE = re.compile(r"(share|stock)\s*(/\s*stock\s*)?price|live\s+nse|nse\s*/\s*bse|technical analysis and", re.I)


def _clean_sector(s) -> str:
    s = strip_emoji(s or "").strip()
    return "" if (not s or "others" in s.lower()) else s


def _asof(df) -> str:
    ix = df.index[-1]
    return ix.strftime("%d-%b-%Y") if hasattr(ix, "strftime") else str(ix)


def _business(inp) -> Optional[str]:
    info = inp.get("info") or {}
    txt = info.get("longBusinessSummary") or info.get("business_summary") or info.get("summary")
    return str(txt).strip()[:600] if txt else None


def _real_news(sym: str) -> List[str]:
    """Headlines only — drops the generic placeholder text market_news falls back to."""
    from market_news import get_stock_news
    try:
        from market_news import _STOCK_NEWS_FALLBACK as FB
        fb_vals = {str(v).strip() for v in FB.values()}
    except Exception:
        fb_vals = set()
    raw = get_stock_news(sym, 5) or ""
    out, seen = [], set()
    for line in raw.split("\n"):
        line = _html.unescape(re.sub(r"<[^>]+>", "", line))
        line = strip_emoji(line).lstrip("• ").strip()
        if (not line or "Recommend checking company announcements" in line
                or line.startswith("Latest market conditions") or line in fb_vals
                or _QUOTE_PAGE_RE.search(line)):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out[:5]


def collect_inputs(sym: str) -> dict:
    from data_engine import get_hist, get_info
    from fundamentals import get_fundamentals
    from market_data import index_history
    from nifty500_collector import get_stock_sector

    df = get_hist(sym, "1y")
    if df is None or df.empty or len(df) < 60:
        raise ValueError(f"Not enough price history for {sym} (need at least 60 sessions).")
    ltp = float(df["Close"].iloc[-1])

    # Stage A — independent fetches in parallel
    with ThreadPoolExecutor(max_workers=8) as pool:
        f_nifty = pool.submit(_safe, index_history, "^NSEI", "1y")
        f_fund = pool.submit(_safe, get_fundamentals, sym, default={})
        f_info = pool.submit(_safe, get_info, sym, default={})
        f_stm = pool.submit(_safe, rd.get_statements, sym)
        f_ext = pool.submit(_safe, rd.get_screener_extras, sym, default={})
        f_cons = pool.submit(_safe, rd.get_consensus, sym, ltp)
        f_news = pool.submit(_safe, _real_news, sym, default=[])
        f_yind = pool.submit(_safe, industry_mod.fetch_yahoo_industry, sym, default={})
        inputs = dict(
            sym=sym, df=df, nifty=f_nifty.result(), fund=f_fund.result() or {},
            info=f_info.result() or {}, stm=f_stm.result(), extras=f_ext.result() or {},
            consensus=f_cons.result(), news=f_news.result() or [],
        )
        yahoo_ind = f_yind.result() or {}
    inputs["name"] = inputs["fund"].get("name") or inputs["info"].get("name") or sym
    sector = get_stock_sector(sym)
    inputs["sector"] = sector

    # Stage B — classify (lender vs corporate) BEFORE peers/scoring, since both depend on it
    ind = industry_mod.classify(inputs["name"], sector, yahoo_ind, inputs["stm"])
    inputs["industry"] = ind
    label, peers, method = _safe(_find_peers, sym, ind.get("kind"), inputs["fund"].get("mcap"), sector,
                                 default=(None, [], "none"))
    inputs.update(peers=peers, peer_label=label, peer_method=method)
    if ind.get("is_lender") and (not sector or "Others" in sector):
        inputs["sector"] = {"Bank": "🏦 Banking", "HFC": "🏠 Housing Finance", "Microfinance": "🏦 Microfinance"}.get(
            ind["kind"], "🏦 NBFC / Lending")
    return inputs


# ══════════════════════════════════════════════════════════════════════════
# STAGE 2 — assemble (engine + charts)
# ══════════════════════════════════════════════════════════════════════════
def assemble(inp: dict, use_ai: bool = True) -> dict:
    sym, name, df = inp["sym"], inp["name"], inp["df"]
    nifty, fund, peers = inp.get("nifty"), inp.get("fund") or {}, inp.get("peers") or []
    sh = (inp.get("extras") or {}).get("shareholding") or []
    has_nifty = nifty is not None and not nifty.empty

    t = eng.compute_technicals(df, nifty)
    rk = eng.compute_risk(df, nifty)
    ind = inp.get("industry") or industry_mod.classify(name, inp.get("sector"), None, inp.get("stm"))
    fv = eng.compute_fundamentals(fund, inp.get("stm"), inp.get("extras") or {}, peers, inp.get("sector"), t["ltp"], ind)
    sc = eng.compute_scores(t, rk, fv)
    plan = eng.build_trade_plan(t, rk, sc)
    fl = eng.build_flags(t, rk, fv, sh, inp.get("consensus"))
    nar = eng.deterministic_narrative(name, sym, t, rk, fv, sc, plan, sh)
    ai_note = eng.ai_analyst_note(name, sym, inp.get("sector"), t, rk, fv, sc, plan, sh) if use_ai else ""

    buzz = None
    try:
        import stocks_buzz
        buzz = stocks_buzz.analyze_stock(sym, df, nifty["Close"] if has_nifty else None)
    except Exception as e:
        logger.debug(f"buzz status skipped: {e}")

    years = (inp.get("stm") or {}).get("years") or []
    ch = {
        "tech": charts.chart_technical(df, sym, t),
        "rel": charts.chart_relative_and_drawdown(df["Close"], nifty["Close"] if has_nifty else None, sym),
        "score": charts.chart_scorecard(sc["pillars"], sc["composite"], sym),
        "fin": charts.chart_financials(years, sym, lender=fv["lender"]) if years else None,
        "sh": charts.chart_shareholding(sh, sym) if sh else None,
    }
    return dict(sym=sym, name=name, sector=_clean_sector(inp.get("sector")), t=t, rk=rk, fv=fv, sc=sc,
                plan=plan, flags=fl, nar=nar, ai_note=ai_note, buzz=buzz, ch=ch, sh=sh,
                stm=inp.get("stm"), peers=peers, consensus=inp.get("consensus"), industry=ind,
                peer_label=inp.get("peer_label"), peer_method=inp.get("peer_method"),
                news=inp.get("news") or [], fund=fund,
                asof=_asof(df), market_ctx=eng.market_context(nifty if has_nifty else None),
                completeness=eng.data_completeness(fv, inp.get("stm"), sh, peers, inp.get("consensus")),
                business=_business(inp), selection=inp.get("selection"),
                generated=datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d-%b-%Y %H:%M IST"))


# ══════════════════════════════════════════════════════════════════════════
# STAGE 3 — build the PDF
# ══════════════════════════════════════════════════════════════════════════
def _stance_color(stance):
    return (GREEN_H if stance.startswith(("HIGH", "CONSTRUCTIVE")) else AMBER_H if stance.startswith(("NEUTRAL", "QUALITY"))
            else RED_H)


def _bias_color(b):
    return GREEN_H if b in ("BULLISH", "FAVOURABLE") else RED_H if b in ("BEARISH", "UNFAVOURABLE") else AMBER_H


def _split_view(R):
    sc, hz = R["sc"], R["sc"]["horizons"]
    lt, st = hz["Long term (1–3 yrs)"], hz["Short term (1–4 wks)"]
    mc = R.get("market_ctx")
    mkt = (f"Market context: {mc['bench']} in a <b>{mc['regime'].lower()}</b> (close {mc['asof']}; "
           f"1M {n(mc['ret_1m'], 1, '%')}, 3M {n(mc['ret_3m'], 1, '%')})." if mc else "Market context: benchmark data unavailable.")
    miss = R["completeness"]["missing_optional"]
    return callout(f"<b>Fundamental assessment:</b> {lt[0].title()} (score {lt[1] if lt[1] is not None else 'n/a'})  ·  "
                   f"<b>Technical timing:</b> {st[0].title()} (score {st[1] if st[1] is not None else 'n/a'}). {mkt} "
                   + (f"Optional data not available: {esc(', '.join(miss))}." if miss else ""), NAVY_H)


def _cover(R, story):
    t, sc, fv, rk = R["t"], R["sc"], R["fv"], R["rk"]
    chg_hex = "#7FE0B0" if t["chg"] >= 0 else "#FF9A9A"
    kind = R["industry"].get("kind") if R.get("industry") else None
    tag = (f"  ·  {esc(R['sector'])}" if R["sector"] else "  ·  Sector unavailable") + (f"  ·  {esc(kind)}" if kind and kind not in (R["sector"] or "") else "")
    banner = Table([
        [P(esc(BRAND.upper()) + "  |  EQUITY RESEARCH", "brand"), ""],
        [P(esc(R["name"]), "title"), P(inr(t["ltp"]), "px")],
        [P(f"NSE: {esc(R['sym'])}{tag}", "sub"),
         P(f"<font color='{chg_hex}'>{t['chg']:+.2f}% today</font>", "pxs")],
    ], colWidths=[W * 0.66, W * 0.34])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("TOPPADDING", (0, 0), (-1, 0), 11), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                                ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 2), (-1, 2), 12),
                                ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 13)]))
    story += [banner, Spacer(1, 3),
              P(f"Prepared by: <b>Senior Market Analyst Desk</b>, {BRAND} (automated research engine)  ·  "
                f"{R['generated']}  ·  Price: close of last completed session ({R['asof']}), not live", "small"), Spacer(1, 8)]

    sc_hex = _stance_color(sc["stance"])
    left = [P("COMPOSITE SCORE", "lab"), P(f"{sc['composite']}<font size=11 color='{GREY_H}'> / 100</font>", "big"),
            P(f"Pillar coverage {sc['pillar_coverage']}% ({sc['n_pillars']}/6)", "tiny"),
            P(f"Mandatory data {R['completeness']['mandatory_ok']}/{R['completeness']['mandatory_total']}", "tiny")]
    mid = [P("ANALYST STANCE", "lab"), P(colored(esc(sc["stance"]), sc_hex), "stance"), Spacer(1, 2), P(esc(sc["action"]))]
    box = Table([[left, mid]], colWidths=[W * 0.24, W * 0.76])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NAVY), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F9FC")),
                             ("LINEAFTER", (0, 0), (0, 0), 0.5, BORDER), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [box, Spacer(1, 5)]
    if sc["veto"]:
        story += [P(colored("Technical veto applied:", AMBER_H) + " composite would read constructive, but price is below key moving averages.", "small"), Spacer(1, 3)]
    fr = fv.get("freshness") or {}
    if fr.get("status") in ("STALE", "LAGGING"):
        story += [P(colored("Data freshness:", AMBER_H) + f" latest quarterly results in our data are {esc(fr.get('label'))}"
                    + (" — latest-quarter growth excluded from scoring." if fr["status"] == "STALE" else " — one reporting cycle behind."), "small"), Spacer(1, 3)]
    if fv.get("lender"):
        story += [P(colored("Lender framework:", NAVY_H) + " scored on balance-sheet growth, leverage, ROE/ROA and P/B — "
                    "operating cash flow is not used (disbursements sit in operating cash flow).", "small"), Spacer(1, 3)]
    if sc["tech_only"]:
        story += [P(colored("Technical-only view:", RED_H) + " fundamental data could not be retrieved for this stock.", "small"), Spacer(1, 3)]

    cells = []
    for k, (lab, score) in sc["horizons"].items():
        cells.append([P(k.upper(), "lab"), P(colored(lab, _bias_color(lab)), "stance"),
                      P(f"score {score}" if score is not None else "insufficient data", "tiny")])
    ht = Table([cells], colWidths=[W / 3] * 3)
    ht.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [ht, Spacer(1, 4), _split_view(R), Spacer(1, 6)]

    risk_hex = {"Low": GREEN_H, "Moderate": AMBER_H, "High": RED_H}.get(rk.get("level"), GREY_H)
    story.append(kv([
        ["Market cap", mcap_txt(fv.get("mcap")), "52-week range", f"{inr(t['w52l'], 0)} – {inr(t['w52h'], 0)}"],
        ["P/E (TTM)  /  Fwd P/E", f"{n(fv.get('pe'), 1)}  /  {n(fv.get('fwd_pe'), 1)}", "ROE  /  ROCE", f"{n(fv.get('roe'), 1, '%')}  /  {n(fv.get('roce'), 1, '%')}"],
        ["Risk level", colored(f"{rk.get('level', 'N/A')} ({n(rk.get('ann_vol'), 1, '%')} vol)", risk_hex), "Beta vs Nifty 50", n(rk.get("beta"), 2)],
    ]))
    story.append(Spacer(1, 6))

    fl = R["flags"]
    s_items = bullets([esc(x) for x in fl["strengths"][:4]], GREEN_H) or [P("No standout strengths identified in the available data.", "small")]
    r_items = bullets([f"<b>{esc(r['title'])}:</b> {esc(r['evidence'])}" for r in fl["risks"][:4]], RED_H) or \
        [P("No major red flags detected in the available data.", "small")]
    sr = Table([[P(colored("WHAT WE LIKE", GREEN_H), "h2"), P(colored("WHAT WORRIES US", RED_H), "h2")],
                [s_items, r_items]], colWidths=[W / 2, W / 2])
    sr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                            ("LINEAFTER", (0, 0), (0, -1), 0.5, BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [sr, Spacer(1, 6)]
    if R["ch"].get("score"):
        im = img(R["ch"]["score"], W)
        if im:
            story.append(im)
    story.append(Spacer(1, 4))
    story.append(P(R["nar"]["summary"], "just"))


def _investment_view(R, story):
    fv = R["fv"]
    story += section("Investment View", "Analyst commentary, bull vs bear case and valuation cross-check")
    ind_ = R.get("industry") or {}
    story += [callout("<b>Business overview:</b> " + (esc(R["business"]) if R.get("business") else
              f"{esc(R['name'])} (NSE: {esc(R['sym'])}); sector: {esc(R['sector']) if R['sector'] else 'not available'}; "
              f"classification: {esc(ind_.get('kind') or 'non-lender corporate')}; market cap {mcap_txt(fv.get('mcap'))}. "
              f"No further business description is available from the configured data sources."), NAVY_H), Spacer(1, 4)]
    if R.get("selection"):
        story += [callout("<b>Why selected:</b> " + esc(R["selection"].get("why", "")), GREEN_H, "#F1FAF5"), Spacer(1, 4)]
    if R["ai_note"]:
        story.append(P(colored("Analyst commentary", NAVY_H) + "  <font size=7 color='#5A6472'>(AI-assisted; every figure verified against the data in this report)</font>", "h2"))
        for para in [p.strip() for p in R["ai_note"].split("\n") if p.strip()]:
            story += [P(esc(para), "just"), Spacer(1, 3)]
    else:
        story.append(P(colored("Analyst commentary", NAVY_H), "h2"))
        story += [P(R["nar"]["outlook"], "just"), Spacer(1, 3)]

    fl = R["flags"]
    if len(fl["strengths"]) > 4 or len(fl["risks"]) > 4:
        sr = Table([[P(colored("BULL CASE", GREEN_H), "h2"), P(colored("BEAR CASE", RED_H), "h2")],
                    [bullets([esc(x) for x in fl["strengths"][:8]], GREEN_H) or [P("No standout strengths in the available data.", "small")],
                     bullets([f"<b>{esc(r['title'])}:</b> {esc(r['evidence'])}" for r in fl["risks"][:8]], RED_H) or [P("No red flags triggered.", "small")]]],
                   colWidths=[W / 2, W / 2])
        sr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                                ("LINEAFTER", (0, 0), (0, -1), 0.5, BORDER), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [Spacer(1, 4), sr]

    rows = []
    if fv.get("implied_pb_value"):
        rows.append([P("Peer P/B cross-check"),
                     P(f"BVPS (price ÷ P/B) × peer-median P/B {fv['peer_pb']}× = <b>{inr(fv['implied_pb_value'])}</b>"),
                     P(colored(f"{fv['implied_pb_upside']:+.1f}% vs CMP", sgn_color(fv["implied_pb_upside"])))])
    if fv.get("implied_peer_value") and not fv.get("lender"):
        rows.append([P("Peer-multiple cross-check"),
                     P(f"EPS {inr(fv['eps'])} × peer-median P/E {fv['peer_pe']}× = <b>{inr(fv['implied_peer_value'])}</b>"),
                     P(colored(f"{fv['implied_upside']:+.1f}% vs CMP", sgn_color(fv["implied_upside"])))])
    cons = R.get("consensus")
    if cons:
        rng = f"  (range {inr(cons['low'], 0)} – {inr(cons['high'], 0)})" if cons.get("low") and cons.get("high") else ""
        rows.append([P("Street consensus (Yahoo)"), P(f"Mean target <b>{inr(cons['mean'], 0)}</b>{rng}"),
                     P(colored(f"{cons['upside']:+.1f}% vs CMP", sgn_color(cons["upside"])))])
    if rows:
        story += [Spacer(1, 6), P("Valuation reference points", "h2"),
                  table([[P("Method", "th"), P("Derivation", "th"), P("Implied", "th")]] + rows, [40 * mm, 98 * mm, 40 * mm]),
                  P("Illustrative references only — a peer-multiple value assumes the stock deserves the peer-median multiple; it is not a price target.", "tiny")]


def _technical(R, story):
    t = R["t"]
    _head = [f for f in section("Technical Analysis", "Multi-timeframe trend, momentum, volume and key price levels")
             if not isinstance(f, CondPageBreak)]
    _im = img(R["ch"]["tech"], W) if R["ch"].get("tech") else None
    story.append(KeepTogether(_head + ([_im] if _im else [])))
    story += [Spacer(1, 5), P(R["nar"]["technical"], "just"), Spacer(1, 6)]

    def trend_cell(lab):
        return colored(lab, _bias_color("BULLISH" if "UP" in lab else "BEARISH" if "DOWN" in lab else "NEUTRAL"))
    story.append(P("Trend & momentum dashboard", "h2"))
    story.append(kv([
        ["Daily trend", trend_cell(t["trend"]), "Weekly trend", trend_cell(t["weekly_trend"]) if t.get("weekly_trend") else "—"],
        ["RSI (14) daily / weekly", f"{t['rsi']}  /  {n(t.get('weekly_rsi'), 1)}", "MACD  (signal, hist)", f"{t['macd']}  ({t['macd_sig']}, {t['macd_hist']})"],
        ["ADX  (+DI / −DI)", f"{t['adx']}  ({t['plus_di']} / {t['minus_di']})", "ATR (14)", f"{inr(t['atr'])}  ({t['atr_pct']}%)"],
        ["EMA 20 / 50", f"{inr(t['ema20'])} / {inr(t['ema50'])}", "EMA 200", inr(t.get("ema200"))],
        ["Bollinger band", f"{inr(t['bb_lo'])} – {inr(t['bb_up'])}", "%B / bandwidth", f"{n(t.get('pct_b'), 0)}%  /  {n(t.get('bandwidth'), 1)}%"],
        ["52W position", f"{n(t.get('pos52'), 0)}% of range  ({t['dist_high']:+.1f}% from high)", "Volume vs 20D avg", f"{n(t.get('vol_ratio'), 2)}×"],
        ["Up/down volume (20D)", f"{n(t.get('updown_vol'), 2)}  —  {t['accumulation'].title()}", "Avg daily turnover", f"₹{n(t.get('traded_cr'), 1)} Cr"],
    ]))

    rets = t.get("returns") or {}
    if rets:
        story += [Spacer(1, 6), P("Performance vs Nifty 50", "h2")]
        hdr = [P("Period", "th"), P("Stock", "thr"), P("Nifty 50", "thr"), P("Relative (pts)", "thr")]
        rows = []
        for lab, d in rets.items():
            rows.append([P(lab, "cellb"), P(colored(f"{d['stock']:+.1f}%", sgn_color(d["stock"])), "cellr"),
                         P(f"{d['nifty']:+.1f}%" if d["nifty"] is not None else "—", "cellr"),
                         P(colored(f"{d['rel']:+.1f}", sgn_color(d["rel"])) if d["rel"] is not None else "—", "cellr")])
        story.append(table([hdr] + rows, [30 * mm, 49 * mm, 49 * mm, 50 * mm]))

    story += [Spacer(1, 6), P("Key price levels", "h2")]
    hdr = [P("Type", "th"), P("Level", "thr"), P("Distance", "thr"), P("Basis", "th")]
    rows = []
    for r in reversed(t["resistances"]):
        rows.append([P(colored("Resistance", RED_H), "cell"), P(inr(r["price"]), "cellrb"), P(f"{r['dist']:+.1f}%", "cellr"), P(esc(r["src"]), "cell")])
    rows.append([P("<b>Current price</b>", "cell"), P(inr(t["ltp"]), "cellrb"), P("—", "cellr"), P("Last close", "cell")])
    for s_ in t["supports"]:
        rows.append([P(colored("Support", GREEN_H), "cell"), P(inr(s_["price"]), "cellrb"), P(f"{s_['dist']:+.1f}%", "cellr"), P(esc(s_["src"]), "cell")])
    story.append(table([hdr] + rows, [30 * mm, 38 * mm, 30 * mm, 80 * mm]))
    pv = t["pivots"]
    story.append(P(f"Classic pivots (last session): S3 {inr(pv['S3'])} · S2 {inr(pv['S2'])} · S1 {inr(pv['S1'])} · "
                   f"<b>P {inr(pv['P'])}</b> · R1 {inr(pv['R1'])} · R2 {inr(pv['R2'])} · R3 {inr(pv['R3'])}", "tiny"))

    b = R.get("buzz")
    if b and b["kind"] in ("BREAKOUT", "WATCH"):
        if b["kind"] == "BREAKOUT":
            when = "today" if b.get("age") == 0 else f"{b.get('age')} session(s) ago"
            txt = (f"<b>Breakout radar:</b> a {b['label']} breakout {when} on {b['vol_ratio']:.1f}× volume "
                   f"(Stocks Buzz score {b['score']:.0f}/100).")
        else:
            txt = f"<b>Breakout radar:</b> trading {b['dist']:.1f}% below its {b['label']} (₹{b['level']:,.2f}) — on the watchlist."
        story += [Spacer(1, 5), callout(txt, GREEN_H, "#F1FAF5")]


def _risk(R, story):
    rk = R["rk"]
    if not rk.get("ann_vol"):
        return
    _head = [f for f in section("Risk & Relative Performance", "How much the stock moves, how deep it falls, and how it tracks the market")
             if not isinstance(f, CondPageBreak)]
    _im = img(R["ch"]["rel"], W) if R["ch"].get("rel") else None
    story.append(KeepTogether(_head + ([_im] if _im else [])))
    story.append(Spacer(1, 5))
    story.append(kv([
        ["Annualised volatility", f"{rk['ann_vol']}%", "Downside volatility", n(rk.get("downside_vol"), 1, "%")],
        ["Max drawdown (1Y)", f"{rk['max_dd']}%", "Current drawdown", f"{rk['cur_dd']}%"],
        ["1-day VaR (95%, historical)", f"{rk['var95']}%", "Worst / best day", f"{rk['worst_day']}% / {rk['best_day']}%"],
        ["Beta vs Nifty 50", n(rk.get("beta"), 2), "Correlation", n(rk.get("corr"), 2)],
    ]))
    story.append(P("VaR: on 95% of days the 1-day loss was smaller than the figure shown (historical, last year). Beta above 1 means the stock tends to move more than the index.", "tiny"))


def _fin_table(rows_def, periods, label_w=40 * mm):
    hdr = [P("", "th")] + [P(esc(y["label"]), "thr") for y in periods]
    data = [hdr]
    for lab, key, fmt in rows_def:
        if all(y.get(key) is None for y in periods):
            continue
        data.append([P(lab, "cellb")] + [P(fmt(y.get(key)) if y.get(key) is not None else "—", "cellr") for y in periods])
    return data, [label_w] + [(W - label_w) / len(periods)] * len(periods)


def _basis_note(fv) -> str:
    bits = []
    for k, lab in (("rev_cagr3", "Sales 3Y"), ("rev_cagr5", "Sales 5Y"), ("pat_cagr3", "Profit 3Y"), ("pat_cagr5", "Profit 5Y")):
        b = (fv.get("cagr_basis") or {}).get(k)
        if b:
            bits.append(f"{lab}: {b}")
        elif (fv.get("cagr_notes") or {}).get(k):
            bits.append(f"{lab}: {fv['cagr_notes'][k]}")
    out = "Basis — annual figures are fiscal-year, reported basis, ₹ Cr; quarterly figures are labelled by quarter. " + "; ".join(bits) + "."
    if fv.get("eps_note"):
        out += " EPS TTM " + fv["eps_note"] + "."
    return out


def _fundamentals(R, story):
    fv, stm = R["fv"], R["stm"]
    if not (any(fv.get(k) is not None for k in ("pe", "roe", "pb", "eps")) or fv["has_statements"]):
        return
    story += section("Fundamental Analysis", "Valuation, growth, profitability and balance sheet" if fv.get("lender")
                     else "Valuation, growth, profitability, balance sheet and cash flow")
    ind = R.get("industry") or {}
    if fv.get("lender"):
        story += [callout(f"<b>Company type: {esc(ind.get('kind') or 'Lender')}.</b> Analysed on balance-sheet growth, leverage, ROE/ROA and P/B. "
                          f"Operating cash flow and debt/equity are deliberately not scored — loan disbursements are classified in operating "
                          f"cash flow under Ind AS, so a growing lender normally shows negative OCF.", NAVY_H), Spacer(1, 5)]
    fr = fv.get("freshness") or {}
    if fr.get("status") in ("STALE", "LAGGING"):
        story += [callout(f"<b>Data freshness:</b> quarterly results in our data end at <b>{esc(fr.get('label'))}</b>; "
                          f"{'results through ' + fr['expected'].strftime('%b %Y') + ' should be public.' if fr.get('expected') else ''} "
                          + ("Latest-quarter growth is excluded from scoring." if fr["status"] == "STALE" else "Possibly a late filing or vendor delay."),
                          AMBER_H, "#FFF9E8"), Spacer(1, 5)]
    if R["nar"].get("fundamental"):
        story += [P(R["nar"]["fundamental"], "just"), Spacer(1, 6)]

    story.append(P("Valuation & returns", "h2"))
    prem = fv.get("pe_prem")
    story.append(kv([
        ["P/E (TTM)", n(fv.get("pe"), 1, "×"), "Forward P/E", n(fv.get("fwd_pe"), 1, "×")],
        ["Peer-median P/E  /  Fwd P/E", f"{n(fv.get('peer_pe'), 1, '×')}  /  {n(fv.get('peer_fwd_pe'), 1, '×')}", "P/E premium / (discount)",
         colored(f"{prem:+.0f}%", sgn_color(prem, good_pos=False)) if prem is not None else "—"],
        ["P/B  /  peer-median P/B", f"{n(fv.get('pb'), 2, '×')}  /  {n(fv.get('peer_pb'), 2, '×')}", "P/B premium / (discount)",
         colored(f"{fv['pb_prem']:+.0f}%", sgn_color(fv["pb_prem"], good_pos=False)) if fv.get("pb_prem") is not None else "—"],
        ["EPS (TTM, derived)" if fv.get("eps_src") == "derived" else "EPS (TTM)", inr(fv.get("eps")), "Earnings yield", n(fv.get("earn_yield"), 1, "%")],
        ["ROE", n(fv.get("roe"), 1, "%"), "ROCE", n(fv.get("roce"), 1, "%")],
        ["Dividend yield", n(fv.get("div_y"), 2, "%"), "PEG (P/E ÷ 3Y profit CAGR)", n(fv.get("peg"), 2)],
        (["ROA (PAT ÷ year-end assets)", n(fv.get("roa"), 2, "%"), "Leverage (assets ÷ net worth)", n(fv.get("leverage"), 1, "×")] if fv.get("lender")
         else ["Debt / Equity", n(fv.get("de"), 2, "×") if not fv["fin"] else "n.m. (financial)", "", ""]),
    ]))

    if fv["has_statements"] or fv["has_quarters"]:
        story += [Spacer(1, 6), P("Growth & earnings quality", "h2"), kv([
            ["Sales CAGR 3Y / 5Y (annual)", f"{n(fv.get('rev_cagr3'), 1, '%')}  /  {n(fv.get('rev_cagr5'), 1, '%')}",
             "Profit CAGR 3Y / 5Y (annual)", f"{n(fv.get('pat_cagr3'), 1, '%')}  /  {n(fv.get('pat_cagr5'), 1, '%')}"],
            ["Latest qtr revenue YoY / QoQ", f"{n(fv.get('q_rev_yoy'), 1, '%')}  /  {n(fv.get('q_rev_qoq'), 1, '%')}",
             "Latest qtr profit YoY / QoQ", f"{n(fv.get('q_pat_yoy'), 1, '%')}  /  {n(fv.get('q_pat_qoq'), 1, '%')}"],
            [f"{'Financing' if fv.get('lender') else 'Operating'} margin, FY ({fv.get('opm_label') or 'n/a'})",
             n(fv.get("opm"), 1, "%") + (f"  ({fv['opm_delta']:+.1f} pts vs 3 FYs ago)" if fv.get("opm_delta") is not None else ""),
             f"Latest-quarter margin ({fv.get('opm_q_label') or 'n/a'})", n(fv.get("opm_q"), 1, "%")],
            ([f"Balance-sheet growth {fv.get('book_growth_yrs') or 3}Y CAGR", f"{n(fv.get('book_growth'), 1, '%')} ({fv.get('book_growth_src') or 'n/a'})",
              "Net worth / borrowings CAGR", f"{n(fv.get('nw_cagr3'), 1, '%')}  /  {n(fv.get('borrow_cagr3'), 1, '%')}"] if fv.get("lender") else
             ["Cash conversion (3Y OCF ÷ PAT)", n(fv.get("ocf_pat"), 2, "×") if not fv["fin"] else "n.m. (financial)",
              "Profit-decline years (last 5)", n(fv.get("down_years"), 0)]),
        ])]

    if fv["has_statements"] or fv["has_quarters"]:
        story.append(P(_basis_note(fv), "tiny"))
    if R["ch"].get("fin"):
        im = img(R["ch"]["fin"], W)
        if im:
            story += [Spacer(1, 6), im]

    years = (stm or {}).get("years") or []
    if len(years) >= 3:
        ann_head = [Spacer(1, 6), P(f"Annual financials ({stm['unit']}; source: {esc(stm['source'])})", "h2")]
        lend = fv.get("lender")
        rows_def = [("Revenue", "revenue", cr), ("Financing profit" if lend else "Operating profit", "op_profit", cr),
                    ("Financing margin %" if lend else "OPM %", "opm", lambda v: n(v, 1)), ("Net profit", "net_profit", cr),
                    ("EPS (₹)", "eps", lambda v: n(v, 2))]
        rows_def += ([("Total assets", "total_assets", cr), ("Borrowings", "borrowings", cr), ("Net worth", "networth", cr)] if lend else
                     [("Operating cash flow", "ocf", cr), ("Borrowings", "borrowings", cr), ("Net worth", "networth", cr),
                      ("ROCE %", "roce", lambda v: n(v, 1))])
        data, widths = _fin_table(rows_def, years[-6:])
        story.append(KeepTogether(ann_head + [table(data, widths)]))

    qs = (stm or {}).get("quarters") or []
    if len(qs) >= 4:
        last6 = qs[-6:]
        data, widths = _fin_table([("Revenue", "revenue", cr), ("Financing profit" if fv.get("lender") else "Operating profit", "op_profit", cr),
                                   ("Financing margin %" if fv.get("lender") else "OPM %", "opm", lambda v: n(v, 1)), ("Net profit", "net_profit", cr),
                                   ("EPS (₹)", "eps", lambda v: n(v, 2))], last6)
        for lab, key in (("Revenue YoY", "revenue"), ("Net profit YoY", "net_profit")):
            cells, ok = [], False
            for i, q in enumerate(last6):
                j = len(qs) - len(last6) + i - 4
                if j >= 0 and qs[j].get(key) and qs[j][key] > 0 and q.get(key) is not None:
                    v = (q[key] / qs[j][key] - 1) * 100
                    cells.append(P(colored(f"{v:+.0f}%", sgn_color(v)), "cellr"))
                    ok = True
                else:
                    cells.append(P("—", "cellr"))
            if ok:
                data.append([P(lab, "cellb")] + cells)
        story.append(KeepTogether([Spacer(1, 6), P("Quarterly trend", "h2"), table(data, widths)]))


def _ownership_peers(R, story):
    sh, peers, fv = R["sh"], R["peers"], R["fv"]
    if len(sh) >= 3:
        story += section("Ownership Pattern", "Who owns the stock and which way the institutional money is moving")
        if R["ch"].get("sh"):
            im = img(R["ch"]["sh"], W)
            if im:
                story.append(im)
        if R["nar"].get("ownership"):
            story += [Spacer(1, 4), P(R["nar"]["ownership"], "just")]
        a, b = (sh[-5] if len(sh) >= 5 else sh[0]), sh[-1]
        hdr = [P("Holder", "th"), P(esc(a["label"]), "thr"), P(esc(b["label"]), "thr"), P("Change (pts)", "thr")]
        rows = []
        for key, lab in (("promoters", holder("promoters")), ("fii", holder("fii")), ("dii", holder("dii")), ("public", holder("public"))):
            if a.get(key) is None or b.get(key) is None:
                continue
            d = b[key] - a[key]
            good = d >= 0 if key != "public" else d <= 0
            rows.append([P(lab, "cellb"), P(f"{a[key]:.1f}%", "cellr"), P(f"{b[key]:.1f}%", "cellr"),
                         P(colored(f"{d:+.1f}", GREEN_H if good else RED_H), "cellr")])
        story += [Spacer(1, 4), KeepTogether([table([hdr] + rows, [50 * mm, 40 * mm, 40 * mm, 48 * mm]),
                  P('Pledged-share data is not available from the configured sources and is not shown.', 'tiny')])]

    if peers:
        title = R.get("peer_label") or R["sector"]
        story += section(f"Peer Comparison — {rg_clean(title)}" if title else "Peer Comparison",
                         "Closest listed peers by market capitalisation  ·  group source: " + (R.get("peer_method") or "sector map"))
        hdr = [P("Company", "th"), P("Mkt cap", "thr"), P("P/E", "thr"), P("Fwd P/E", "thr"), P("P/B", "thr"),
               P("ROE %", "thr"), P("1Y return", "thr")]
        r1 = (R["t"].get("returns") or {}).get("1Y") or {}
        pct1 = lambda v: f"{v:+.1f}%" if v is not None else "—"
        me = [P(f"<b>{esc(R['name'])}</b> (this stock)", "cell"), P(mcap_txt(fv.get("mcap")), "cellrb"),
              P(n(fv.get("pe"), 1), "cellrb"), P(n(fv.get("fwd_pe"), 1), "cellrb"), P(n(fv.get("pb"), 2), "cellrb"),
              P(n(fv.get("roe"), 1), "cellrb"), P(pct1(r1.get("stock")), "cellrb")]
        rows = [me]
        for p in peers:
            rows.append([P(esc(p["name"]), "cell"), P(mcap_txt(p["mcap"]), "cellr"), P(n(p.get("pe"), 1), "cellr"),
                         P(n(p.get("fwd_pe"), 1), "cellr"), P(n(p.get("pb"), 2), "cellr"), P(n(p.get("roe"), 1), "cellr"),
                         P(pct1(p.get("ret_1y")), "cellr")])
        med_ret = eng._median([p.get("ret_1y") for p in peers])
        rows.append([P("<b>Peer median</b>", "cell"), P("—", "cellr"), P(n(fv.get("peer_pe"), 1), "cellrb"),
                     P(n(fv.get("peer_fwd_pe"), 1), "cellrb"), P(n(fv.get("peer_pb"), 2), "cellrb"),
                     P(n(fv.get("peer_roe"), 1), "cellrb"), P(pct1(med_ret), "cellrb")])
        extra = [("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#E4ECF7")),
                 ("BACKGROUND", (0, len(rows)), (-1, len(rows)), colors.HexColor("#EEF1F6"))]
        story.append(table([hdr] + rows, [50 * mm, 26 * mm, 17 * mm, 18 * mm, 16 * mm, 19 * mm, 32 * mm], extra=extra))
        notes = []
        if fv.get("pe_prem") is not None:
            notes.append(f"trades at a {abs(fv['pe_prem']):.0f}% {'premium' if fv['pe_prem'] >= 0 else 'discount'} to the peer-median P/E")
        if fv.get("lender") and fv.get("pb_prem") is not None:
            notes.append(f"a {abs(fv['pb_prem']):.0f}% {'premium' if fv['pb_prem'] >= 0 else 'discount'} to peer-median P/B")
        if fv.get("roe") is not None and fv.get("peer_roe") is not None:
            notes.append(f"its ROE is {'above' if fv['roe'] >= fv['peer_roe'] else 'below'} the peer median ({fv['roe']:.1f}% vs {fv['peer_roe']:.1f}%)")
        if notes:
            story += [Spacer(1, 4), P(f"{esc(R['name'])} " + " and ".join(notes) + ".", "just")]
        if len(peers) < 3:
            story.append(P(f"Only {len(peers)} peer(s) returned data — treat peer medians with caution.", "tiny"))
    elif R.get("peer_label"):
        story += section("Peer Comparison", "Peer lookup")
        story.append(callout(f"A peer group was identified ({esc(R['peer_label'])}, {esc(R.get('peer_method') or '')}) but none of its members "
                             f"returned valuation data right now. Peer-relative valuation is therefore omitted from scoring.", AMBER_H, "#FFF9E8"))


def rg_clean(x: str) -> str:
    return esc(strip_emoji(x or ""))


def _plan(R, story):
    t, plan = R["t"], R["plan"]
    story += section("Scenarios & Trade Plan", "Rule-based levels derived from ATR, support/resistance and realised volatility")
    label, hexcol, bg, msg = {
        "ACTIVE": ("Active setup", GREEN_H, "#F1FAF5", "Trend and momentum support a long setup. Use staggered entries near the zone below and respect the stop."),
        "CONDITIONAL": ("Conditional setup", AMBER_H, "#FFF9E8", "No clean edge yet. Treat the levels below as valid only after the bullish trigger confirms."),
        "DEFENSIVE": ("Defensive — no fresh long setup", RED_H, "#FDF1F1", "Price structure and/or the composite score do not support new long positions. Wait for the reclaim level below."),
    }[plan["mode"]]
    story += [callout(f"<b>{label}.</b> {msg}", hexcol, bg), Spacer(1, 6)]

    if plan["mode"] != "DEFENSIVE":
        def rr(p):
            return f"{(p - t['ltp']) / (t['ltp'] - plan['sl']):.1f}R" if t["ltp"] > plan["sl"] else "—"

        def pct(p):
            return f"{(p / t['ltp'] - 1) * 100:+.1f}%"
        hdr = [P("Level", "th"), P("Price", "thr"), P("vs CMP", "thr"), P("Reward : risk", "thr")]
        rows = [
            [P("Entry zone", "cellb"), P(f"{inr(plan['entry_lo'])} – {inr(plan['entry_hi'])}", "cellrb"), P("—", "cellr"), P("—", "cellr")],
            [P(colored("Stop-loss", RED_H), "cellb"), P(inr(plan["sl"]), "cellrb"), P(pct(plan["sl"]), "cellr"), P("1R risk", "cellr")],
            [P(colored("Target 1", GREEN_H), "cellb"), P(inr(plan["t1"]), "cellrb"), P(pct(plan["t1"]), "cellr"), P(rr(plan["t1"]), "cellr")],
            [P(colored("Target 2", GREEN_H), "cellb"), P(inr(plan["t2"]), "cellrb"), P(pct(plan["t2"]), "cellr"), P(rr(plan["t2"]), "cellr")],
            [P(colored("Target 3", GREEN_H), "cellb"), P(inr(plan["t3"]), "cellrb"), P(pct(plan["t3"]), "cellr"), P(rr(plan["t3"]), "cellr")],
        ]
        story.append(table([hdr] + rows, [48 * mm, 52 * mm, 36 * mm, 42 * mm]))
        story.append(P(f"Stop sits below the nearest support (or 2×ATR where none is close). Targets are 1.5R / 2.5R / 4R. "
                       f"Daily ATR is {inr(t['atr'])} ({t['atr_pct']}% of price).", "tiny"))
        sz = plan["sizing"]
        if sz["qty"] > 0:
            story += [Spacer(1, 5), P("Position-sizing example", "h2"),
                      P(f"Capital {inr(sz['capital'], 0)}, risking {sz['risk_pct']}% = {inr(sz['risk_amt'], 0)}. "
                        f"Quantity = risk amount ÷ (entry − stop) = <b>{sz['qty']:,} shares</b> "
                        f"(≈ {inr(sz['value'], 0)}, {sz['pct_capital']}% of capital). Scale to your own capital and risk tolerance.", "just")]
    else:
        recl = plan.get("reclaim")
        story.append(P(f"<b>Watchlist view — no entry, stop or target levels are given.</b> A <b>confirmed</b> re-entry needs a daily close above "
                       f"{inr(plan['confirm_level'])} with RSI back above 50 and above-average volume. "
                       f"<b>Further downside risk</b> opens below {inr(plan['bear_trigger'])}.", "just"))

    _trig_head = [Spacer(1, 7), P("Decision triggers", "h2")]
    res, sup = t["resistances"], t["supports"]
    bull_obj = inr(res[1]["price"]) if len(res) > 1 else inr(plan["t2"])
    bear_obj = inr(sup[1]["price"]) if len(sup) > 1 else inr(t["ema200"] or t["w52l"])
    base = [P(colored("Base", AMBER_H), "cellb"), P(f"Consolidation between {inr(plan['bear_trigger'])} and {inr(plan['bull_trigger'])}"), P("Range-bound; wait for resolution")]
    bear = [P(colored("Bear", RED_H), "cellb"), P(f"Daily close below {inr(plan['bear_trigger'])}"), P(f"Next support {bear_obj}")]
    if plan["mode"] == "DEFENSIVE":
        rows = []
        if plan["confirm_level"] > plan["early_trigger"] + 1e-9:
            rows.append([P(colored("Early recovery", AMBER_H), "cellb"), P(f"Daily close above {inr(plan['early_trigger'])} — not a confirmed entry"),
                         P(f"Next resistance {bull_obj}; still inside a downtrend")])
        rows += [[P(colored("Confirmed re-entry", GREEN_H), "cellb"),
                  P(f"Daily close above {inr(plan['confirm_level'])}, RSI above 50, above-average volume"), P("Re-assess for a long setup")], base, bear]
    else:
        rows = [[P(colored("Bull", GREEN_H), "cellb"), P(f"Daily close above {inr(plan['bull_trigger'])} on ≥1.5× average volume"), P(f"Next resistance {bull_obj}")], base, bear]
    story.append(KeepTogether(_trig_head + [table([[P("Scenario", "th"), P("Condition", "th"), P("Objective", "th")]] + rows, [34 * mm, 90 * mm, 54 * mm])]))
    r1 = plan.get("range_1m")
    if r1:
        story += [Spacer(1, 6), P("One-month statistical range (from realised volatility)", "h2"),
                  P(f"±1σ ({r1['sigma_pct']}%): <b>{inr(r1['s1_lo'])} – {inr(r1['s1_hi'])}</b> (about 68% of outcomes)  ·  "
                    f"±2σ: <b>{inr(r1['s2_lo'])} – {inr(r1['s2_hi'])}</b> (about 95%). "
                    f"This is a volatility-implied range, not a forecast.", "just")]


def _risks_catalysts(R, story):
    story += section("Risk Matrix & Catalysts", "Every flag below is triggered by a specific number in this report")
    risks = R["flags"]["risks"]
    if risks:
        rows = [[P(colored(r["sev"].upper(), RED_H if r["sev"] == "High" else AMBER_H), "cellb"),
                 P(f"<b>{esc(r['title'])}</b>"), P(esc(r["evidence"]))] for r in risks]
        story.append(table([[P("Severity", "th"), P("Risk", "th"), P("Evidence", "th")]] + rows, [22 * mm, 46 * mm, 110 * mm]))
    else:
        story.append(callout("No major red flags were triggered by the available price, risk and fundamental data.", GREEN_H, "#F1FAF5"))
    notes = R["flags"].get("notes") or []
    if notes:
        story += [Spacer(1, 8), P("Analyst notes & data limitations", "h2")] + bullets([esc(x) for x in notes], AMBER_H)
    news = [x for x in (R["news"] or []) if isinstance(x, dict) and x.get("title") and x.get("source") and x.get("date")]
    if news:
        story += [Spacer(1, 8), P("Recent headlines", "h2")]
        story += bullets([f"{esc(x['title'])} — {esc(x['source'])}, {esc(x['date'])}" for x in news[:5]])
        story.append(P("Headlines are listed for context only and have not been used in the scoring.", "tiny"))


def _appendix(R, story):
    story += section("Appendix — Methodology, Data & Disclaimer")
    story.append(P("Scoring framework", "h2"))
    desc = {
        "Trend": "Price vs EMA 20/50/200, EMA alignment, weekly trend, ADX strength and direction",
        "Momentum": "RSI zone, MACD, 3-month relative strength vs Nifty, 1-month return",
        "Quality": "ROE, ROCE, leverage, cash conversion, margin trend, earnings consistency",
        "Valuation": "P/E vs peers and absolute, PEG, P/B for financials",
        "Growth": "3-year sales & profit CAGR, latest-quarter YoY growth",
        "Risk": "Volatility, max drawdown, beta, liquidity (higher score = safer)",
    }
    rows = [[P(k, "cellb"), P(f"{int(w * 100)}%", "cellr"), P(desc[k])] for k, w in eng.WEIGHTS.items()]
    story.append(table([[P("Pillar", "th"), P("Weight", "thr"), P("What it measures", "th")]] + rows, [28 * mm, 20 * mm, 130 * mm]))
    sc_, cm = R["sc"], R["completeness"]
    arith = " + ".join(f"{k} {sc_['pillars'][k]}×{sc_['weights_eff'][k]:.1f}%" for k in sc_["weights_eff"])
    story.append(P(f"<b>Composite arithmetic:</b> {arith} = {sc_['composite_raw']} → rounded half-up to <b>{sc_['composite']}</b> "
                   f"(the stance band uses the rounded value). "
                   + ("All six pillars were available, so no re-weighting was needed." if sc_["n_pillars"] == 6 else
                      f"Missing pillars ({', '.join(sc_['missing_pillars'])}) were excluded and the other weights re-normalised to 100%."), "tiny"))
    story.append(P("Cost-of-equity hurdle used in the low-ROE flag: " + f"{R['fv'].get('coe', eng.COE_ASSUMED):.0f}% — a fixed assumption, not a CAPM estimate. "
                   "Pillars without data are excluded and the remaining weights re-normalised (see 'Pillar coverage' on page 1). "
                   "Stance bands: 75+ high-conviction constructive · 62–74 constructive · 48–61 neutral/wait · 35–47 cautious · below 35 negative. "
                   "A technical veto caps the stance at neutral when the trend pillar is below 35. "
                   "The framework is a structured summary of the data, not a forecast and not a back-tested trading system.", "tiny"))

    story += [Spacer(1, 8), P("Data sources & completeness", "h2")]
    stm = R["stm"]
    cm = R["completeness"]
    items = [
        f"Prices & volume: Yahoo Finance / NSE / Stooq daily candles ({R['t']['bars']} sessions); benchmark Nifty 50.",
        (f"Financial statements: {esc(stm['source'])} — {len(stm.get('years') or [])} annual periods, "
         f"{len(stm.get('quarters') or [])} quarters.") if stm else "Financial statements: not available.",
        f"Field completeness — mandatory: {cm['mandatory_ok']}/{cm['mandatory_total']}"
        + (f" (missing: {esc(', '.join(cm['missing_mandatory']))})" if cm["missing_mandatory"] else "")
        + f"; optional: {cm['optional_ok']}/{cm['optional_total']}"
        + (f" (not available: {esc(', '.join(cm['missing_optional']))})" if cm["missing_optional"] else "")
        + f". Pillar coverage: {R['sc']['n_pillars']}/6.",
        f"Price timestamp: close of {R['asof']} (last completed session, not live). Benchmark: Nifty 50"
        + (f", observation to {R['market_ctx']['asof']}." if R.get("market_ctx") else " (benchmark data unavailable)."),
        f"Shareholding: Screener.in, {len(R['sh'])} quarters." if R["sh"] else "Shareholding: not available.",
        (f"Peers: {len(R['peers'])} companies from the '{rg_clean(R.get('peer_label'))}' group ({esc(R.get('peer_method') or '')})."
         if R["peers"] else "Peers: no peer group could be resolved or no peer returned data."),
        ("Company classification: " + esc((R.get("industry") or {}).get("kind") or "non-lender corporate")
         + (" — detected via " + "; ".join(esc(x) for x in (R["industry"].get("reasons") or [])) if (R.get("industry") or {}).get("reasons") else "") + "."),
        (f"Quarterly data freshness: {R['fv']['freshness'].get('status')} (latest {esc(R['fv']['freshness'].get('label'))}; "
         f"sources — annual: {esc((stm or {}).get('sources', {}).get('years') or 'n/a')}, quarterly: {esc((stm or {}).get('sources', {}).get('quarters') or 'n/a')})."
         if (R["fv"].get("freshness") or {}).get("status") else "Quarterly data freshness: not available."),
        "Analyst commentary: " + ("AI-assisted, with every figure checked against the data above." if R["ai_note"] else "rule-generated (no AI text used)."),
    ]
    story += bullets(items)
    story += [Spacer(1, 8), P("Glossary", "h2"),
              P("<b>ATR</b> average true range, a typical daily move · <b>ADX</b> trend strength (above 25 = trending) · <b>RSI</b> momentum oscillator (70/30 bands) · "
                "<b>EMA</b> exponential moving average · <b>OPM</b> operating profit margin · <b>ROCE/ROE</b> return on capital/equity · "
                "<b>PEG</b> P/E divided by profit growth · <b>R-multiple</b> reward as a multiple of the risk taken · <b>VaR</b> value-at-risk · "
                "<b>σ</b> standard deviation of returns.", "small")]
    story += [Spacer(1, 10), HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=5),
              P("<b>Disclaimer.</b> This report is generated automatically from public market data and, where indicated, AI-assisted text. "
                "It is for educational and informational purposes only and does not constitute investment advice, a recommendation, or an offer to buy or sell any security. "
                "The author is not a SEBI-registered Research Analyst or Investment Adviser. Data may be delayed, incomplete or inaccurate. "
                "Past performance and technical signals do not guarantee future results. Equity investments are subject to market risk, including loss of capital. "
                "Consult a SEBI-registered adviser before making investment decisions.", "disc")]


def build_pdf(R: dict, output_path: str) -> str:
    story: list = []
    _cover(R, story)
    story.append(PageBreak())
    _investment_view(R, story)
    _technical(R, story)
    _risk(R, story)
    _fundamentals(R, story)
    _ownership_peers(R, story)
    _plan(R, story)
    _risks_catalysts(R, story)
    _appendix(R, story)

    doc = SimpleDocTemplate(output_path, pagesize=A4, topMargin=19 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title=f"{strip_emoji(R['name'])} — Equity Research Report", author=BRAND)
    meta = {"sym": R["sym"], "name": strip_emoji(R["name"]), "generated": R["generated"]}
    doc.build(story, canvasmaker=make_canvas(meta))
    return output_path


# ══════════════════════════════════════════════════════════════════════════
# Public entrypoint (signature unchanged)
# ══════════════════════════════════════════════════════════════════════════
def generate_stock_report_pdf(symbol: str) -> Tuple[bool, str, Optional[str]]:
    sym = str(symbol).upper().replace(".NS", "").replace(".BO", "").strip()
    if not sym:
        return False, "No symbol provided.", None
    try:
        inputs = collect_inputs(sym)
        R = assemble(inputs)
        out = os.path.join(OUTPUT_DIR, f"{sym}_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
        build_pdf(R, out)
        return True, out, R["name"]
    except ValueError as e:
        return False, str(e), None
    except Exception as e:
        logger.error(f"[Report] generation failed for {sym}: {e}", exc_info=True)
        return False, f"Report generation failed: {e}", None
