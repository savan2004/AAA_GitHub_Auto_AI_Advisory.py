# Manual financial data fetch (isolated)

This command fetches **one** Stoxim API response and prints it, then exits.
No scheduling, database writes, Telegram integration, or website scraping.
Stoxim currently advertises a free API plan, but requires an API key and has
coverage and usage limitations. Check provider terms before storing or reusing data.

Install: `python -m pip install requests`

Set `STOXIM_API_KEY` as a private environment variable (never commit it).

Run: `python -m standalone_collector.manual_fetch INE040A01034 --module financials`

Optional local output:
`python -m standalone_collector.manual_fetch INE040A01034 --module ratios --output result.json`

Offline tests: `python -m unittest standalone_collector.test_manual_fetch`

A live fetch is **not verified** until this command succeeds with a valid key
on a machine with working DNS/network access. This module does not invoke
the older continuous worker in `collector.py`.
