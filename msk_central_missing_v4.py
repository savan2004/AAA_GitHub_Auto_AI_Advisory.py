"""Build a public-only AI recovery worklist, never market observations.

Each shadow run emits an atomic JSON file. It is a dated snapshot, not a
durable cross-run ledger. A lead remains unverified until independent source
verification. The worklist contains no prices, holdings or credentials.
"""
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile
from msk_central_data_v4 import normalize_symbol

MAX_SYMBOLS = 700


def build_recovery_bank(report, session):
    if type(session) is not date:
        raise ValueError("session date required")
    stocks = report.get("stocks", {})
    if not isinstance(stocks, dict) or len(stocks) > MAX_SYMBOLS:
        raise ValueError("invalid stock report")
    tasks = {}
    def add(symbol, isin, dataset, reason, period):
        key = (symbol, isin, dataset, period)
        item = tasks.setdefault(key, {"symbol":symbol, "isin":isin, "dataset":dataset,
            "period":period, "reasons":[], "status":"pending_evidence",
            "ai_action":"discover_original_source_only", "publish_allowed":False})
        if reason not in item["reasons"]:
            item["reasons"].append(reason)
    for symbol, row in sorted(stocks.items()):
        if normalize_symbol(symbol) != symbol or not isinstance(row, dict):
            raise ValueError("invalid stock identity")
        isin = row.get("isin")
        if not isinstance(isin,str) or not re.fullmatch(r"IN[A-Z0-9]{9}[0-9]",isin):
            raise ValueError("invalid stock ISIN")
        if row.get("history_shape_valid") is not True:
            add(symbol,isin,"historical_ohlcv","history_missing_or_invalid",session.isoformat())
        if row.get("latest_raw_ohlcv_match") is not True:
            add(symbol,isin,"daily_ohlcv","latest_bar_not_crosschecked",session.isoformat())
        if row.get("technical_calculated") is not True:
            add(symbol,isin,"technical_inputs","technical_calculation_withheld",session.isoformat())
        # This pilot does not independently verify every financial field.
        # Even a partial official comparison must not close the recovery item.
        if row.get("financial_status") != "fresh_secondary_candidate":
            add(symbol,isin,"fundamental_evidence","fresh_financial_data_unavailable","latest_reported_quarter")
        add(symbol,isin,"fundamental_evidence","independent_field_verification_pending","latest_reported_quarter")
        if row.get("official_quarter_comparison") == "mismatch":
            add(symbol,isin,"fundamental_evidence","conflicting_financial_evidence","latest_reported_quarter")
    items = list(tasks.values())
    return {"schema_version":1,"project":"MSK","session":session.isoformat(),
        "pilot_status":report.get("status","unknown"),"expected_stock_count":report.get("target"),
        "observed_stock_count":len(stocks),
        "collection_blocker":"roster_or_report_unavailable" if not stocks else None,
        "pending_items":len(items),"pending_stock_count":len({x["symbol"] for x in items}),
        "items":items,"policy":"AI may find evidence links only; independent verification required before use."}


def write_recovery_bank(path, bank):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,
                                         prefix=".msk-recovery-",delete=False) as out:
            tmp = out.name
            json.dump(bank,out,indent=2,sort_keys=True)
            out.write("\n")
        os.replace(tmp,path)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
