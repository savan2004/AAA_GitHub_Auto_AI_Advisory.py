# MSK overnight NSE full-universe audit

Only MSK Bot. Official indexes: Nifty 100, Nifty Midcap 150, Nifty Smallcap 250.

The scheduled script attempts each constituent in durable batches. PostgreSQL retains audits even for missing price data and incomplete fundamental verification.

Configure GitHub Actions secret MSK_RESEARCH_DATABASE_URL, and the same environment variable on Render MSK Bot. Both require access to the same PostgreSQL database.

Scheduled IST collection: 16:15, 20:30, 01:00, 05:00, 07:15. Publication attempt: 07:45. GitHub Actions scheduling can be delayed; 08:00 availability is a target, not a guaranteed SLA.

All members must have audit rows for a report to publish. Individual financial values may still be UNVERIFIED. API availability is not guaranteed.

The Telegram button reads the completed report first. When missing, legacy on-demand screening remains as a fallback until rollout is verified.

Test using: python -m unittest tests.test_overnight_index_research
Verify Actions logs, database coverage, deployment and real Telegram output before claiming live completion.

Exchange holidays need a proper trading calendar before treating the schedule as production-grade.