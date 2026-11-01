# MSK Bot — Monthly Outlook publication procedure

Scope: MSK Bot only. Data is read from data/monthly_outlook/YYYY-MM.json. No nightly bot computation, AI-generated forecasts, or automatic market-data retrieval is implemented by this feature.

## Publication steps

1. After the most recent completed NSE session, assemble evidence for Nifty, Bank Nifty, breadth, sector rotation, volatility and major risk events. Record every observation's as-of date, source and data-quality limitation separately in your editorial evidence file.
2. Prepare the month's short plain-language report: broad market bias, index scenarios and invalidation conditions, strong/weak sectors, key events, scenario risks and watchlist (only when verified). Never invent prices, target levels, trading signals or confidence/accuracy figures.
3. Independent manual reviewer checks all claims and data age. A second reviewer approves publication. Treat missing essential evidence as no report.
4. Add exactly one file named YYYY-MM.json under data/monthly_outlook with all required fields: month, approved (boolean), published_at (YYYY-MM-DD), as_of (YYYY-MM-DD), title, summary, risk_notice. Drafts must set approved=false. After review, change approved to true and commit it in a reviewed PR.
5. Test: python -m unittest discover -s tests -p 'test_monthly_outlook.py' -v. Inspect CI. Only after review and approved deployment should /outlook and the button display it. /outlook YYYY-MM reads previous archived editions; missing or unapproved editions show an explicit no-report message.
6. To withdraw an edition, use a reviewed commit setting approved=false or remove the archive file. Redeploy and verify.

Example **DRAFT ONLY — not market research**:

    {
      "month": "2026-11",
      "approved": false,
      "published_at": "2026-11-01",
      "as_of": "2026-10-30",
      "title": "DRAFT — November 2026 Monthly Market Outlook",
      "summary": "DRAFT ONLY: Insert independently verified index trend, sector positioning, risks, scenarios and evidence notes before approval.",
      "risk_notice": "Educational research. Not SEBI-registered investment advice."
    }

## Still required for launch readiness

* Reliable NSE/alternate price collectors with freshness and licensing checks.
* Evidence ledger and reviewer workflow for each month.
* Complete quantitative reports and optional 2–4 page PDF, with as-of timestamps, data coverage, and risk scenarios.
* Production Telegram acceptance tests, permission/rate-limit checks and operational monitoring.
* Immutable historical edition/version history and replacement/withdrawal audit trail.

Do not publish the draft sample as an approved report.
