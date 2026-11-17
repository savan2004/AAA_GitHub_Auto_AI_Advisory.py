"""Private one-shot, cash-inclusive Portfolio Doctor scenario (feature-flagged).

The customer supplies assumptions and a *proposed* ticker-weight shortlist.
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
    "CONSTRAINTS", "PDF_FAILED", "TIMEOUT", "WORKER_FAILED",
))
REQUIRED = frozenset(("CAPITAL", "HORIZON", "RISK", "CASH", "STOCKCAP", "SECTORCAP"))


class BuilderError(RuntimeError):
    def __init__(self, code="BAD_INPUT"):
        code = code if code in ALLOWED_ERRORS else "WORKER_FAILED"
        self.code = code
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
    if not 3 <= len(lines) <= 7:
        raise BuilderError()
    if lines[0].split("@", 1)[0].strip().lower() != "/doctor_builder":
        raise BuilderError()
    settings = {}
    for token in lines[1].split():
        if token.count("=") != 1:
            raise BuilderError()
        k, v = token.split("=", 1)
        k = k.upper()
        if k not in REQUIRED | {"EXCLUDE"} or k in settings or not v.strip():
            raise BuilderError()
        settings[k] = v
    if not REQUIRED.issubset(settings):
        raise BuilderError()
    try:
        if not re.fullmatch(r"[0-9]{1,3}", settings["HORIZON"]):
            raise ValueError("bad_horizon")
        if settings["RISK"].lower() not in ("low", "medium", "high"):
            raise ValueError("bad_risk")
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
            "excluded": sorted(excludes), "targets": positions,
        }
    except (ValueError, TypeError, OverflowError) as exc:
        raise BuilderError() from exc


def build_scenario_current_closes(spec, history_fetcher=None, sector_resolver=None, *, now=None):
    """Price exactly every requested candidate, or withhold the entire plan."""
    from portfolio_doctor_live import verified_completed_daily_frame, PortfolioDoctorError
    from portfolio_doctor_advanced import build_portfolio_scenario, DoctorEvidenceError
    from market_data import bulk_history
    from nifty500_collector import get_stock_sector
    history_fetcher = history_fetcher or bulk_history
    sector_resolver = sector_resolver or get_stock_sector
    names = [row["symbol"] for row in spec["targets"]]
    try:
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
    except DoctorEvidenceError as exc:
        raise BuilderError("CONSTRAINTS") from exc
    if len(result.get("proposed") or []) != len(names):
        raise BuilderError("CONSTRAINTS")
    for out, inp in zip(result["proposed"], priced):
        out["price_date"] = inp["price_date"]
    return result


def builder_pdf_bytes(spec, scenario):
    """Two-page or shorter ReportLab PDF, built strictly in memory."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    w, h = A4
    x, y = 42, h - 52
    def print_line(value, size=9, bold=False, gap=16):
        nonlocal y
        if y < 75:
            pdf.showPage()
            y = h - 52
        pdf.setFillColor(HexColor("#18334E"))
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, str(value)[:106])
        y -= gap

    print_line("MSK | PORTFOLIO DOCTOR: OPTIONAL BUILDER", 15, True, 27)
    print_line("On-demand hypothetical scenario | " + datetime.now(IST).strftime("%d %b %Y %H:%M IST"), 9)
    print_line("New allocation from supplied capital ONLY; no earlier holdings assumed.", 9)
    print_line("Prices: latest completed NSE daily candles from Yahoo / provider fallback.", 8)
    print_line("These are NOT live or guaranteed executable quotes.", 8, gap=23)
    print_line("CUSTOMER ASSUMPTIONS", 11, True, 22)
    print_line("Capital Rs {:,.2f} | Horizon {} months | Risk category {}".format(
        scenario["planned_new_capital"], spec["horizon_months"], spec["risk_tolerance"]), 9)
    print_line("Minimum cash Rs {:,.2f} | Stock cap {}% | Sector cap {}%".format(
        float(spec["liquidity_needed"]), spec["stock_cap_pct"], spec["sector_cap_pct"]), 9)
    print_line("Excluded from new purchases: {}".format(
        ", ".join(spec["excluded"]) if spec["excluded"] else "None"), 9, gap=22)

    print_line("PROPOSED WHOLE-SHARE MODEL", 11, True, 23)
    for row in scenario["proposed"]:
        print_line("{} | {} shares | last close Rs {:,.2f} ({})".format(
            row["symbol"], row["shares"], row["price"], row["price_date"]), 9, True)
        print_line("  Target {}% | Model value Rs {:,.2f} | Actual weight {:.2f}%".format(
            row["requested_weight_pct"], row["new_value"],
            row["proposed_total_weight_pct"]), 8, gap=21)
    print_line("Cash after rounding: Rs {:,.2f} ({:.2f}%)".format(
        scenario["cash"], scenario["cash_weight_pct"]), 10, True)
    print_line("Total including cash: {:.4f}%".format(
        scenario["total_weights_pct"]), 10, True, 21)
    print_line("Underweight by design: remaining capital stays in cash.", 9)
    print_line("Sector and single-stock caps checked on rounded final weights.", 9, gap=22)
    print_line("RESEARCH & PRIVACY LIMITATIONS", 10, True, 19)
    print_line("This is user-directed arithmetic, not a risk-optimised recommendation.", 8)
    print_line("Corporate actions and official issuer prices are not independently certified.", 8)
    print_line("Do not treat technical/quality scores as win probabilities.", 8)
    print_line("No orders or swaps; verify prices and liquidity before any investment.", 8)
    print_line("No holdings or PDF are saved in the MSK database. Telegram retains chats/files.", 8)
    print_line("Educational only; not SEBI-registered personalised investment advice.", 8)
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
        raise BuilderError(code)
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
        sys.stderr.write("\nMSK_BUILDER_CODE=" + exc.code + "\n")
        raise SystemExit(1)
    except Exception:
        sys.stderr.write("\nMSK_BUILDER_CODE=WORKER_FAILED\n")
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(2)
    _worker_main()
