"""Non-customer public-source check. No Telegram, client holdings or persistent files."""
from portfolio_doctor_advanced import _screener_same_page_evidence

def main():
    extras, ratios = _screener_same_page_evidence("INFY")
    years = extras.get("years") or []
    quarters = extras.get("quarters") or []
    scope = extras.get("_statement_scope") or "unverified"
    matching = ratios.get("_screener_scope") == scope and scope in ("consolidated", "standalone")
    print("MSK_DOCTOR_PUBLIC_SOURCE_PROBE status={} scope={} years={} quarters={} ratio_scope_match={} issuer_filing_verified=NO".format(
        "PASS" if (years and quarters and matching) else "UNAVAILABLE",
        scope, len(years), len(quarters), bool(matching)))
    if not years or not quarters or not matching:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
