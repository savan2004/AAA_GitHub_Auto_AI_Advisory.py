"""MSK lightweight, free-first incident control. Stdlib only; read-only by default.

Usage: python -m tools.msk_repair_control --input sample.log --output incidents.json
No LLM calls, secrets, writes to production DB, or auto deployment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RULES = (
    ("SECURITY_SECRET", "P0", r"(?i)(secret|token|api.key).{0,40}(leak|expos|print|logged)"),
    ("DATABASE_FAILURE", "P0", r"(?i)(postgres|psycopg|database|connection pool).{0,60}(fail|error|exhaust|timeout)"),
    ("PORTFOLIO_INTEGRITY", "P0", r"(?i)(portfolio|holding).{0,70}(corrupt|lost|overwrite|duplicate|missing storage)"),
    ("MARKET_DATA_FAILURE", "P0", r"(?i)(all sources failed|no price data found|yfpricesmissingerror|stale quote|invalid price)"),
    ("SCANNER_TIMEOUT", "P1", r"(?i)(futures unfinished|screener.{0,30}timeout|timeouterror)"),
    ("PROVIDER_AUTH", "P1", r"(?i)(http.error.? 401|http.error.? 403|unauthorized|forbidden)"),
    ("PROVIDER_RATE_LIMIT", "P1", r"(?i)(http.error.? 429|rate.limit|too many requests)"),
    ("WEBHOOK_FAILURE", "P1", r"(?i)(webhook.{0,40}(error|fail)|telegram.{0,40}(timeout|error))"),
    ("CI_FAILURE", "P1", r"(?i)(pytest.{0,40}failed|ci.{0,40}failed|test.{0,40}failed)"),
    ("RESOURCE_FAILURE", "P1", r"(?i)(out of memory|oomkilled|disk full|no space left)"),
)
REDACT = (
    (re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)postgres(?:ql)?://[^\s]+"), "[REDACTED_DATABASE_URL]"),
)
def sanitize(line: str) -> str:
    for pattern, replacement in REDACT:
        line = pattern.sub(replacement, line)
    return line[:500]

def classify(line: str) -> tuple[str, str]:
    for code, severity, pattern in RULES:
        if re.search(pattern, line):
            return code, severity
    return "UNKNOWN", "P2"

def analyze(lines: list[str]) -> dict:
    incidents: dict[str, dict] = {}
    for line in lines:
        if not line.strip():
            continue
        clean = sanitize(line.strip())
        code, severity = classify(clean)
        signature = hashlib.sha256((code + ":" + re.sub(r"\d+", "#", clean.lower())).encode()).hexdigest()[:12]
        key = code + "-" + signature
        if key not in incidents:
            incidents[key] = {"id": key, "category": code, "priority": severity,
                              "count": 0, "sample": clean,
                              "action": "review_required" if severity == "P0" else "diagnose_and_test"}
        incidents[key]["count"] += 1
    counts = Counter(item["category"] for item in incidents.values() for _ in range(item["count"]))
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only", "total_events": sum(counts.values()),
            "categories": dict(counts), "incidents": list(incidents.values())}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = analyze(args.input.read_text(encoding="utf-8", errors="replace").splitlines())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"MSK incidents: {report['total_events']} events; {len(report['incidents'])} signatures; read-only")

if __name__ == "__main__":
    main()
