"""Standalone, read-only MSK Web Advisory research API (Python stdlib only).

This server is NOT installed in the bot, does not import bot modules, and is
never launched by the existing Render static-site service.
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_ORIGIN = os.environ.get("MSK_WEB_ALLOWED_ORIGIN", "https://msk-web-advisory-preview.onrender.com")
DATA_DIR = os.environ.get("MSK_WEB_DATA_DIR", "")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._&-]{0,19}$")
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
MAX_BYTES = 256 * 1024
MAX_BARS = 400


class ProviderError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status
        super().__init__(code)


def validated_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol) or symbol.startswith("DEMO"):
        raise ProviderError("invalid_symbol", 400)
    return symbol


class LocalCSVProvider:
    """Provider boundary. Replace only this adapter after source/licensing review.

    Files must be locally provisioned by trusted operations, never uploaded by
    anonymous clients. No arbitrary host fetching, SQL or file-path inputs.
    """

    def __init__(self, directory: str):
        self.directory = Path(directory).resolve() if directory else None

    def history(self, symbol: str) -> dict:
        symbol = validated_symbol(symbol)
        if self.directory is None:
            raise ProviderError("provider_not_configured", 503)
        filepath = self.directory / (symbol + ".csv")
        if not filepath.is_file():
            raise ProviderError("symbol_not_available", 404)
        if filepath.stat().st_size > MAX_BYTES:
            raise ProviderError("source_too_large", 422)
        try:
            with filepath.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
                    raise ProviderError("invalid_source_columns", 422)
                bars = []
                for row in reader:
                    if len(bars) >= MAX_BARS:
                        raise ProviderError("too_many_bars", 422)
                    session = date.fromisoformat(row["timestamp"])
                    if session > date.today():
                        raise ProviderError("future_session", 422)
                    values = {k: float(row[k]) for k in FIELDS[1:]}
                    import math
                    if not all(math.isfinite(v) for v in values.values()):
                        raise ProviderError("nonfinite_value", 422)
                    if not (values["low"] > 0 and values["low"] <= min(values["open"], values["close"])
                            and values["high"] >= max(values["open"], values["close"])
                            and values["volume"] >= 0):
                        raise ProviderError("invalid_candle", 422)
                    if bars and row["timestamp"] <= bars[-1]["timestamp"]:
                        raise ProviderError("unordered_or_duplicate_dates", 422)
                    bars.append({"timestamp": session.isoformat(), **values})
        except (ValueError, KeyError, TypeError, UnicodeError, csv.Error, OSError):
            raise ProviderError("invalid_source_data", 422) from None
        if len(bars) < 50:
            raise ProviderError("insufficient_history", 422)
        # Explicitly user/operator-provisioned data; never mislabel as NSE verified.
        return {"symbol": symbol, "exchange": "NSE", "interval": "1d",
                "source": "operator_provisioned_csv", "verified": False,
                "live": False, "adjusted": False, "asof": bars[-1]["timestamp"],
                "count": len(bars), "bars": bars}


def make_handler(provider: LocalCSVProvider):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Prevent URLs and user-supplied query strings from entering app logs.
            pass

        def send_json(self, status: int, payload: dict):
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            origin = self.headers.get("Origin", "")
            if origin == ALLOWED_ORIGIN:
                self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/health", "/api/health"):
                return self.send_json(200, {"ok": True, "service": "msk-web-research-api",
                                             "provider": "configured" if provider.directory else "disabled",
                                             "live_market_feed": False, "ai_enabled": False})
            match = re.fullmatch(r"/api/v1/equities/([^/]+)/history", path)
            if not match:
                return self.send_json(404, {"error": "not_found"})
            try:
                data = provider.history(match.group(1))
                return self.send_json(200, data)
            except ProviderError as exc:
                return self.send_json(exc.status, {"error": exc.code})
            except Exception:
                # Fail closed: never expose filesystem paths or exception details.
                return self.send_json(503, {"error": "provider_unavailable"})

        def do_POST(self):
            self.send_json(405, {"error": "read_only_service"})

        def do_OPTIONS(self):
            # No cross-origin writes and no credentialed CORS.
            self.send_json(405, {"error": "read_only_service"})
    return Handler


def serve():
    host = os.environ.get("MSK_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    if not (1 <= port <= 65535):
        raise ValueError("invalid PORT")
    ThreadingHTTPServer((host, port), make_handler(LocalCSVProvider(DATA_DIR))).serve_forever()


if __name__ == "__main__":
    serve()
