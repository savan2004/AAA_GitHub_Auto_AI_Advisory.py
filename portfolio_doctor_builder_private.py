"""Private one-shot, cash-inclusive Portfolio Doctor scenario (feature-flagged).

The customer supplies assumptions and may request strictly verified automatic selection.
No holdings, request, PDFs, trades or delivery ledger are saved to disk or DB.
Only the parent process's RAM carries a request; worker stdin/stdout carries bytes.
Market quotes are latest completed daily candles, never live execution prices.
"""
import io
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MAX_INPUT_BYTES = 2048
MAX_PDF_BYTES = 2 * 1024 * 1024
IST = timezone(timedelta(hours=5, minutes=30))
ALLOWED_ERRORS = frozenset((
    "BAD_INPUT", "NO_PRICES", "STALE_PRICES", "UNKNOWN_SECTOR",
    "CONSTRAINTS", "NO_QUALIFIED_PICKS", "PDF_FAILED", "TIMEOUT", "WORKER_FAILED",
))
REQUIRED = frozenset(("CAPITAL", "HORIZON", "RISK", "CASH", "STOCKCAP", "SECTORCAP"))


class BuilderError(RuntimeError):
    def __init__(self, code="BAD_INPUT", *, detail=None):
        code = code if code in ALLOWED_ERRORS else "WORKER_FAILED"
        self.code = code
        self.detail = detail if code == "CONSTRAINTS" else None
        super().__init__(code)


def parse_builder_request(data):
    """Parse the entire one-message request. No remembered holdings or user IDs."""
    if isinstance(data, bytes):
        if not 0 < len(data) <= MAX_INPUT_BYTES:
            raise BuilderError()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BuilderError() from exc
    elif isinstance(data, str):
        text = data
        if not 0 < len(text.encode("utf-8")) <= MAX_INPUT_BYTES:
            raise BuilderError()
    else:
        raise BuilderError()
    if "\x00" in text:
        raise BuilderError()
    lines = [part.strip() for part in text.splitlines() if part.strip()]
    if not 2 <= len(lines) <= 7:
        raise BuilderError()
    if lines[0].split("@", 1)[0].strip().lower() != "/doctor_builder":
        raise BuilderError()
    settings = {}
    for token in lines[1].split():
        if token.count("=") != 1:
            raise BuilderError()
        k, v = token.split("=", 1)
        k = k.upper()
        if k not in REQUIRED | {"EXCLUDE", "AUTO_SELECT"} or k in settings or not v.strip():
            raise BuilderError()
        settings[k] = v
    if not REQUIRED.issubset(settings):
        raise BuilderError()
    auto = settings.get("AUTO_SELECT") == "TRUE"
    if ("AUTO_SELECT" in settings and not auto) or (len(lines) != 2 if auto else len(lines) < 3):
        raise BuilderError()
    try:
        if not re.fullmatch(r"[0-9]{1,3}", settings["HORIZON"]):
            raise ValueError("bad_horizon")
        if settings["RISK"].lower() not in ("low", "medium", "high"):
            raise ValueError("bad_risk")
        if auto and int(settings["HORIZON"]) not in (6, 18, 36, 60):
            raise ValueError("bad_horizon")
        if auto:
            for field in ("CAPITAL", "CASH", "STOCKCAP", "SECTORCAP"):
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", settings[field]):
                    raise ValueError("bad_numeric")
            amount, cash = float(settings["CAPITAL"]), float(settings["CASH"])
            stock_cap, sector_cap = float(settings["STOCKCAP"]), float(settings["SECTORCAP"])
            if not (0 < amount <= 1e12 and 0 <= cash <= amount and
                    0 < stock_cap <= 100 and 0 < sector_cap <= 100):
                raise ValueError("bad_limits")
        for key in ("CAPITAL", "CASH", "STOCKCAP", "SECTORCAP"):
            value = float(settings[key])
            if not math.isfinite(value):
                raise ValueError("nonfinite")
        excludes = {v.upper() for v in settings.get("EXCLUDE", "").split(",") if v}
        if any(not re.fullmatch(r"[A-Z0-9&-]{2,20}", sym) for sym in excludes):
            raise ValueError("bad_exclusions")
        if len(excludes) > 25:
            raise ValueError("too_many_exclusions")
        positions, seen = [], set()
        for row in lines[2:]:
            parts = row.split()
            if len(parts) != 2:
                raise ValueError("bad_row")
            sym, raw_pct = parts
            sym = sym.upper().removesuffix(".NS")
            if sym in seen or sym in excludes or not re.fullmatch(r"[A-Z0-9&-]{2,20}", sym):
                raise ValueError("bad_symbol")
            pct = float(raw_pct)
            if not math.isfinite(pct) or pct <= 0 or pct > 100:
                raise ValueError("bad_weight")
            seen.add(sym)
            positions.append({"symbol": sym, "target_weight_pct": pct})
        if sum(item["target_weight_pct"] for item in positions) > 100 + 1e-8:
            raise ValueError("weights_above_100")
        return {
            "capital": settings["CAPITAL"], "horizon_months": int(settings["HORIZON"]),
            "risk_tolerance": settings["RISK"].lower(),
            "liquidity_needed": settings["CASH"],
            "stock_cap_pct": settings["STOCKCAP"],
            "sector_cap_pct": settings["SECTORCAP"],
            "excluded": sorted(excludes), "targets": positions, "auto_select": auto,
        }
    except (ValueError, TypeError, OverflowError) as exc:
        raise BuilderError() from exc


def _automatic_targets(spec, history_fetcher, *, now=None, universe_fetcher=None, assessor=None):
    """Choose only evidence-qualified NSE members; no fake picks or orders."""
    from index_constituents import get_index_universe
    from top5_fundamental_gate import fetch_and_assess
    from portfolio_doctor_live import verified_completed_daily_frame, PortfolioDoctorError
    from nifty500_collector import get_stock_sector

    profile = {"low": "conservative", "medium": "moderate",
               "high": "aggressive"}[spec["risk_tolerance"]]
    universe_fetcher = universe_fetcher or get_index_universe
    assessor = assessor or fetch_and_assess
    try:
        universe = universe_fetcher(profile)
        if universe.get("stale") or not universe.get("symbols") or not universe.get("industries"):
            raise BuilderError("NO_QUALIFIED_PICKS")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise BuilderError("NO_QUALIFIED_PICKS") from exc

    # NSE's official constituents, spread across industries before price fetch.
    names, industry_counts = [], {}
    for sym in universe["symbols"]:
        sector = str(universe["industries"].get(sym) or "").strip()
        if (sym in spec["excluded"] or not re.fullmatch(r"[A-Z0-9&-]{2,20}", sym)
                or not sector or sector.lower() in ("unknown", "unclassified")
                or "other" in sector.lower() or industry_counts.get(sector, 0) >= 3):
            continue
        names.append(sym)
        industry_counts[sector] = industry_counts.get(sector, 0) + 1
        if len(names) == 36:
            break
    if len(names) < 3:
        raise BuilderError("NO_QUALIFIED_PICKS")
    try:
        frames = history_fetcher(names, "1y", chunk_size=8, retries=0,
                                 max_fallbacks=5, download_timeout=12)
    except Exception as exc:
        raise BuilderError("NO_QUALIFIED_PICKS") from exc
    if not isinstance(frames, dict):
        raise BuilderError("NO_QUALIFIED_PICKS")

    length = {6: 120, 18: 160, 36: 200, 60: 220}[spec["horizon_months"]]
    momentum = {6: 63, 18: 120, 36: 180, 60: 200}[spec["horizon_months"]]
    vol_max = {"low": .38, "medium": .55, "high": .80}[spec["risk_tolerance"]]
    dd_min = {"low": -.30, "medium": -.45, "high": -.60}[spec["risk_tolerance"]]
    today = (now or datetime.now(IST)).astimezone(IST).date()
    technical = []
    for sym in names:
        try:
            frame = verified_completed_daily_frame(frames.get(sym), now=now)
            if len(frame) < length:
                continue
            for col in ("Open", "High", "Low", "Close", "Volume"):
                series = frame[col].astype(float)
                if not series.notna().all() or not (series > 0).all():
                    raise ValueError("missing_daily_bar")
            if (frame["High"].astype(float) < frame["Low"].astype(float)).any():
                continue
            close = frame["Close"].astype(float)
            gain = float(close.iloc[-1] / close.iloc[-momentum - 1] - 1)
            vol = float(close.pct_change().iloc[-90:].std() * math.sqrt(252))
            draw = float((close.iloc[-180:] / close.iloc[-180:].cummax() - 1).min())
            if gain <= 0 or not math.isfinite(vol) or vol > vol_max or draw < dd_min:
                continue
            technical.append((sym, frame, gain, vol))
        except (PortfolioDoctorError, ValueError, KeyError, TypeError, OverflowError,
                IndexError, AttributeError):
            continue
    technical.sort(key=lambda row: (-row[2], row[3], row[0]))
    candidates = []
    for sym, frame, gain, vol in technical[:12]:
        industry = universe["industries"][sym]
        try:
            evidence, reason = assessor(sym, industry, profile, today=today)
            score = float(evidence["fund_score"]) if reason == "qualified" else float("nan")
            if not math.isfinite(score) or score <= 0:
                continue
            broad_sector = get_stock_sector(sym)
            sector = (broad_sector if broad_sector and "others" not in broad_sector.lower()
                      else industry)
            candidates.append((score, gain, -vol, sym, sector, frame))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        except Exception:
            continue
    candidates.sort(reverse=True)

    selected, used_sectors = [], set()
    for _score, _gain, _negvol, sym, sector, frame in candidates:
        if sector in used_sectors:
            continue
        selected.append((sym, sector, frame))
        used_sectors.add(sector)
        if len(selected) >= 5:
            break
    if len(selected) < 3:
        raise BuilderError("NO_QUALIFIED_PICKS")
    capital = float(spec["capital"])
    equity_pct = (capital - float(spec["liquidity_needed"])) * 100 / capital
    weight = round(min(equity_pct / len(selected),
                       float(spec["stock_cap_pct"]), float(spec["sector_cap_pct"])), 2)
    if weight <= 0:
        raise BuilderError("NO_QUALIFIED_PICKS")
    priced = []
    for sym, sector, frame in selected:
        close = float(frame["Close"].iloc[-1])
        if capital * weight / 100 < close:
            raise BuilderError("NO_QUALIFIED_PICKS")
        priced.append({"symbol": sym, "sector": sector, "price": close,
                       "target_weight_pct": weight, "fresh_price_verified": True,
                       "price_date": frame.index[-1].date().isoformat()})
    return priced


def build_scenario_current_closes(spec, history_fetcher=None, sector_resolver=None, *, now=None, universe_fetcher=None, assessor=None):
    """Withhold incomplete models; auto path never uses customer holdings."""
    from portfolio_doctor_live import verified_completed_daily_frame, PortfolioDoctorError
    from portfolio_doctor_advanced import (
        BuilderConstraintError, DoctorEvidenceError, build_portfolio_scenario,
    )
    from market_data import bulk_history
    from nifty500_collector import get_stock_sector
    history_fetcher = history_fetcher or bulk_history
    sector_resolver = sector_resolver or get_stock_sector
    try:
        if spec.get("auto_select"):
            priced = _automatic_targets(spec, history_fetcher, now=now,
                                        universe_fetcher=universe_fetcher, assessor=assessor)
        else:
            names = [row["symbol"] for row in spec["targets"]]
            try:
                frames = history_fetcher(names, "1y", chunk_size=5, retries=0,
                                         max_fallbacks=5, download_timeout=12)
            except Exception as exc:
                raise BuilderError("NO_PRICES") from exc
            priced = []
            for target in spec["targets"]:
                sym = target["symbol"]
                try:
                    frame = verified_completed_daily_frame(frames.get(sym), now=now)
                    close = float(frame["Close"].iloc[-1])
                    if not math.isfinite(close) or close <= 0:
                        raise BuilderError("NO_PRICES")
                except PortfolioDoctorError as exc:
                    raise BuilderError("STALE_PRICES" if exc.code == "STALE_PRICES" else "NO_PRICES") from exc
                except (TypeError, ValueError, OverflowError, IndexError, KeyError) as exc:
                    raise BuilderError("NO_PRICES") from exc
                try:
                    sector = sector_resolver(sym)
                except Exception:
                    sector = None
                if not sector or "others" in str(sector).lower() or "unknown" in str(sector).lower():
                    raise BuilderError("UNKNOWN_SECTOR")
                priced.append({
                    **target, "price": close, "sector": sector,
                    "fresh_price_verified": True,
                    "price_date": frame.index[-1].date().isoformat(),
                })
        result = build_portfolio_scenario(
            capital=spec["capital"], horizon_months=spec["horizon_months"],
            risk_tolerance=spec["risk_tolerance"],
            liquidity_needed=spec["liquidity_needed"],
            stock_cap_pct=spec["stock_cap_pct"], sector_cap_pct=spec["sector_cap_pct"],
            excluded=spec["excluded"],
            candidates=priced, current_values=None, funding_mode="additional",
        )
    except BuilderConstraintError as exc:
        raise BuilderError("CONSTRAINTS", detail=exc.detail) from exc
    except DoctorEvidenceError as exc:
        raise BuilderError("CONSTRAINTS") from exc
    if len(result.get("proposed") or []) != len(priced):
        raise BuilderError("CONSTRAINTS")
    for out, inp in zip(result["proposed"], priced):
        out["price_date"] = inp["price_date"]
    if spec.get("auto_select"):
        result["warnings"] = ["Qualified NSE members and completed prices; risk/horizon research screen.",
                              "No trades or orders placed."]
    return result


def builder_pdf_bytes(spec, scenario):
    """Render a one-page allocation dashboard in memory."""
    from collections import defaultdict
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase.pdfmetrics import stringWidth

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    width, height = A4
    left, right = 42, width - 42
    navy, blue, muted = "#18334E", "#1769A9", "#51657A"
    pale, white = "#EDF3F8", "#FFFFFF"

    def label(value, x, y, *, size=9, color=navy, bold=False, limit=None):
        font = "Helvetica-Bold" if bold else "Helvetica"
        value = str(value)
        if limit and stringWidth(value, font, size) > limit:
            while stringWidth(value + "...", font, size) > limit:
                value = value[:-1]
            value += "..."
        pdf.setFillColor(HexColor(color))
        pdf.setFont(font, size)
        pdf.drawString(x, y, value)

    def bar(x, y, percent, color, *, span):
        pdf.setFillColor(HexColor(color))
        pdf.rect(x, y, span * max(0, min(100, percent)) / 100, 5, stroke=0, fill=1)

    capital = scenario["planned_new_capital"]
    cash = scenario["cash"]
    minimum = float(spec["liquidity_needed"])
    sector_cap = float(spec["sector_cap_pct"])
    rows = scenario["proposed"]
    sectors = defaultdict(float)
    for row in rows:
        sectors[row["sector"]] += row["proposed_total_weight_pct"]

    pdf.setFillColor(HexColor(navy))
    pdf.roundRect(left, height - 115, right - left, 75, 8, stroke=0, fill=1)
    label("MSK | PORTFOLIO BUILDER", left + 15, height - 68, size=19, color=white, bold=True)
    label("Hypothetical allocation | " + datetime.now(IST).strftime("%d %b %Y %H:%M IST"),
          left + 15, height - 91, color=white)

    cards = (
        ("CAPITAL", capital),
        ("MODELED STOCK VALUE", capital - cash),
        ("CASH LEFT", cash),
    )
    card_width = (right - left - 20) / 3
    for i, (title, value) in enumerate(cards):
        x = left + i * (card_width + 10)
        pdf.setFillColor(HexColor(pale))
        pdf.roundRect(x, 650, card_width, 59, 6, stroke=0, fill=1)
        label(title, x + 9, 690, size=8, bold=True)
        label(f"Rs {value:,.2f}", x + 9, 669, size=11, bold=True, limit=card_width - 18)
    label(f"Cash minimum Rs {minimum:,.2f} | Above minimum Rs {cash - minimum:,.2f} | "
          f"Cash weight {scenario['cash_weight_pct']:.2f}%", left, 631)
    label(f"Stock cap {float(spec['stock_cap_pct']):.2f}% | Sector cap {sector_cap:.2f}% | "
          f"Horizon {spec['horizon_months']} months | Risk preference {spec['risk_tolerance']}",
          left, 614, size=8, color=muted)

    label("TARGET VS. WHOLE-SHARE ALLOCATION", left, 587, size=11, bold=True)
    label("Bars: actual in blue, target or cap in pale blue", 333, 587, size=7.5, color=muted)
    label("Stock / completed close", left, 565, size=8, bold=True)
    label("Shares", 169, 565, size=8, bold=True)
    label("Target / actual", 218, 565, size=8, bold=True)
    label("Modeled value", 316, 565, size=8, bold=True)
    label("Below target", 444, 565, size=8, bold=True)
    y = 545
    for row in rows:
        target = row["requested_weight_pct"]
        actual = row["proposed_total_weight_pct"]
        value = row["new_value"]
        gap = max(0, capital * target / 100 - value)
        label(row["symbol"], left, y, bold=True)
        label(f"Rs {row['price']:,.2f} | {row['price_date']}", left, y - 12, size=7, limit=124)
        label(f"{row['shares']:,}", 169, y, size=8, limit=47)
        label(f"{target:.2f}% / {actual:.2f}%", 218, y, size=8)
        bar(218, y - 12, 100, pale, span=83)
        bar(218, y - 12, target, "#A7BDD1", span=83)
        bar(218, y - 12, actual, blue, span=83)
        label(f"Rs {value:,.2f}", 316, y, size=8)
        label(f"Rs {gap:,.2f}", 444, y, size=8)
        y -= 32

    label("SECTOR EXPOSURE VS. CAP", left, y - 4, size=11, bold=True)
    label("Actual % and remaining room (percentage points)", left, y - 20, size=8, color=muted)
    y -= 43
    for sector, weight in sorted(sectors.items(), key=lambda item: (-item[1], item[0])):
        label(sector, left, y, size=8, limit=160)
        bar(209, y, 100, pale, span=120)
        bar(209, y, sector_cap, "#A7BDD1", span=120)
        bar(209, y, weight, blue, span=120)
        label(f"{weight:.2f}% of {sector_cap:.2f}% cap | {max(0, sector_cap - weight):.2f} pp left",
              340, y, size=8)
        y -= 18

    label("METHOD AND COVERAGE", left, y - 8, size=9, bold=True)
    if spec.get("auto_select"):
        label("Shares screened with NSE membership, fundamentals and completed daily closes.",
              left, y - 23, size=8)
        label("Risk/horizon and caps set research weights. Not an order or trade signal.",
              left, y - 37, size=8)
    else:
        label("Balances use your chosen targets and completed closes. Cash includes rounding.",
              left, y - 23, size=8)
        label("Risk and horizon are context only for manual stock targets.",
              left, y - 37, size=8)
    label("Source: Yahoo / provider fallback. Completed closes can differ from executable prices.",
          left, y - 51, size=8)
    if spec["excluded"]:
        label("Excluded symbols: " + ", ".join(spec["excluded"]), left, y - 65, size=8,
              limit=right - left)
    label("PRIVATE | Educational model | No automatic orders", left, 48, size=8, color=muted)
    pdf.showPage()
    pdf.save()
    raw = buf.getvalue()
    if not raw.startswith(b"%PDF-") or not (1000 <= len(raw) <= MAX_PDF_BYTES):
        raise BuilderError("PDF_FAILED")
    return raw


def run_isolated_builder(payload, timeout=120):
    """Stateless public-markets worker; no request details in arguments or logs."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_INPUT_BYTES:
        raise BuilderError("BAD_INPUT")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        child = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker"],
                               input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise BuilderError("TIMEOUT") from exc
    except OSError as exc:
        raise BuilderError("WORKER_FAILED") from exc
    if child.returncode:
        code = "WORKER_FAILED"
        for line in child.stderr[-1024:].decode("utf-8", "replace").splitlines():
            if line.startswith("MSK_BUILDER_CODE="):
                candidate = line.split("=", 1)[1].strip()
                if candidate in ALLOWED_ERRORS:
                    code = candidate
        detail = None
        if code == "CONSTRAINTS" and 0 < len(child.stdout) <= 512:
            candidate = child.stdout.decode("utf-8", "replace")
            if candidate.isprintable():
                detail = candidate
        raise BuilderError(code, detail=detail)
    raw = child.stdout
    if not raw.startswith(b"%PDF-") or not (1000 <= len(raw) <= MAX_PDF_BYTES):
        raise BuilderError("PDF_FAILED")
    try:
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(raw), strict=True).pages
        if not 1 <= len(pages) <= 3:
            raise BuilderError("PDF_FAILED")
    except BuilderError:
        raise
    except Exception as exc:
        raise BuilderError("PDF_FAILED") from exc
    return raw


def _worker_main():
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        spec = parse_builder_request(payload)
        result = build_scenario_current_closes(spec)
        pdf = builder_pdf_bytes(spec, result)
        sys.stdout.buffer.write(pdf)
    except BuilderError as exc:
        if exc.detail:
            sys.stdout.buffer.write(exc.detail.encode("utf-8")[:512])
        sys.stderr.write("\nMSK_BUILDER_CODE=" + exc.code + "\n")
        raise SystemExit(1)
    except Exception:
        sys.stderr.write("\nMSK_BUILDER_CODE=WORKER_FAILED\n")
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(2)
    _worker_main()
