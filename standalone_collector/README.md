# Isolated fundamental collector (draft; NOT deployed)

This directory is independent of main.py, Telegram, existing cron jobs and the website.
It does not send messages, make trades, or read/modify the existing bot database.

## Operation
1. Provision a **separate PostgreSQL database** with TLS certificate verification, least-privilege credentials and automated backups.
2. Contract with an API provider that explicitly permits storage, refresh, and commercial use of fundamentals. Set COLLECTOR_API_BASE_URL to its HTTPS adapter endpoint and COLLECTOR_API_TOKEN if required. The endpoint must return JSON at /fundamentals/{NSE_SYMBOL}, containing exact symbol, provider, HTTPS source_url, YYYY-MM statement_period and numeric fundamental metrics.
3. Set COLLECTOR_DATABASE_URL to the new PostgreSQL connection string; use a trusted CA and valid hostname.
4. Install `requests` and `psycopg[binary]` in an isolated Python environment.
5. Seed symbols using `python -m standalone_collector.collector --seed-file standalone_collector/universe.txt --once` (provide your own file). Then run `python -m standalone_collector.collector` as an **independent managed background worker**, with restart-on-failure and monitoring. Do not run this inside the bot web process.
6. Run offline tests: `python -m unittest discover -s standalone_collector -p 'test_*.py'`.

## Behaviour
- At most 20 due symbols per pass, sequentially with request timeouts and pacing.
- Successful records refresh after 7 days; failed records retry with exponential delay up to 24 hours.
- Existing verified snapshots survive failed refreshes; audit attempts are recorded.
- No unapproved Screener scraping. No default provider, no fake financial figures.
- This is a collector foundation, not a turnkey connection to EODHD/Yahoo/Screener: a licensed provider adapter and actual runtime must be configured and tested.
- This draft does **not** expose a bot query API or an on-demand queue. No production configuration is changed.
- For 24/7 service use a paid/eligible always-on worker or other reliable scheduler; free tiers may sleep.
