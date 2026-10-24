# Reliability deployment checklist

- Set `PORTFOLIO_DB_PATH` to an absolute path on an attached **persistent** disk (for example `/var/data/portfolio.sqlite3`). The default local SQLite file is NOT durable on Render's ephemeral filesystem.
- Render's free web plan does not provide a persistent disk. Move to a plan with persistent disk or use a managed external database before enabling production portfolios.
- Existing `portfolio_data.json` records are **not migrated automatically**. Back them up and migrate before switching production traffic.
- Set `WEBHOOK_URL`, `TELEGRAM_WEBHOOK_SECRET`, and `TELEGRAM_TOKEN` in Render environment variables.
- Webhook processing now completes before HTTP 200, so lengthy synchronous Telegram handlers should be offloaded to the existing executor. If Telegram request deadlines are exceeded, migrate to a durable queue.
- `/` is process liveness only; `/api/status` reports whether a Telegram update was processed in the last five minutes. An idle bot is not necessarily unhealthy.
- Run `python -m unittest discover -v` and review GitHub Actions before merging.
- Do not merge until production persistence, backup, and webhook behavior are verified.
