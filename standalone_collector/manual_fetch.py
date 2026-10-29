"""Manual, read-only Stoxim API fetch. No bot, DB, scheduling or scraping."""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit
import requests

BASE = "https://api.stoxim.in/v1"
MODULES = ("financials", "ratios", "shareholding")

def get_data(isin, module, key, session=None, base=BASE):
    if not isin.startswith("IN") or len(isin) != 12 or not isin.isalnum():
        raise ValueError("Provide a valid 12-character Indian ISIN")
    if module not in MODULES:
        raise ValueError("Unsupported module")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("HTTPS API base required")
    if not key:
        raise ValueError("STOXIM_API_KEY is missing")
    client = session or requests
    response = client.get(
        f"{base.rstrip('/')}/{module}/{isin}",
        headers={"X-API-Key": key, "Accept": "application/json"},
        timeout=(5, 20), allow_redirects=False,
    )
    response.raise_for_status()
    if len(response.content) > 500_000:
        raise ValueError("Response too large")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected API response")
    if payload.get("status") == "error":
        raise ValueError("Provider reported an error")
    return payload

def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch and print one financial record, then exit")
    parser.add_argument("isin", help="12-character Indian ISIN")
    parser.add_argument("--module", choices=MODULES, default="financials")
    parser.add_argument("--output", help="Optional local JSON output path")
    args = parser.parse_args(argv)
    data = get_data(args.isin.upper(), args.module, os.getenv("STOXIM_API_KEY", ""))
    output = json.dumps(data, indent=2, ensure_ascii=False)
    print(output)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, requests.RequestException) as exc:
        print(f"FETCH FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
