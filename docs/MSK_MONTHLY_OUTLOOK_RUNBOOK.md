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


## Single-task generation pipeline (PR #93)

The dedicated workflow .github/workflows/monthly-outlook.yml runs offline tests on PRs.
After the workflow is merged into main it is scheduled for 07:00 IST on calendar days 1-5
and runs the data collector on the first weekday only. Indian exchange holidays are not
independently inferred. Use workflow_dispatch to retry after a holiday or collector outage.

Pipeline: sector_report.collect -> sector_report.analyze -> validation of same-session Nifty,
Bank Nifty, >=5 sector indices and >=60% constituent coverage -> deterministic scenarios ->
UNAPPROVED monthly JSON and clearly labelled PDF -> GitHub Actions draft artifact.

It refuses stale (older than 7 calendar days), future-dated, absent, and undercovered
evidence. It makes no Telegram post, no approved archive change, no auto-merge and no
automatic trading recommendation. Missing data aborts the draft rather than substituting
invented prices. Publishing remains a manual, reviewed commit setting approved to true.

**How to accept an edition**:
1. Download the Actions artifact and compare every index/sector level with completed
   market-session source records and independent evidence; assess macro dates and risks.
2. Refuse the edition if relevant prices, timestamp, provider provenance, sector sample,
   event calendar, or PDF content cannot be verified.
3. Edit the approved content; change approved to true only when signed off and
   published_at is the actual publication date; commit data/monthly_outlook/YYYY-MM.json.
4. Run offline tests, obtain green CI, merge and deploy an authorized release.
5. Check /outlook and /outlook YYYY-MM on the real Telegram bot; record message
   acceptance and archive consistency. Confirm HTML escaping and missing-month behavior.

**Known limitations**: The collector is dependent on MSK's existing free market-data
providers and may fail. First-weekday scheduling is not exchange-holiday-aware; an owner
must review or manually rerun it. Complete economic event calendar and fundamental
forecast verification require independent editorial inputs; no AI accuracy guarantee.
The first completed real-data collection, publication approval, Render deployment and
Telegram live acceptance are not certified by PR CI alone.
