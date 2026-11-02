# MSK Manual Bot — Cleanup Register (26 Oct 2026)

Goal: keep the interactive Telegram stock-analysis bot; do not run scheduled channel publishing or scheduled development.

## KEEP — verified useful
- `main.py`: Flask/Telegram webhook and user commands.
- `config.py`, `data_engine.py`, `technical_indicators.py`, `api_utils.py`: configuration, data and analysis.
- `stocks_buzz.py`, `universe.py`, `market_data.py`: user-requested Stocks Buzz.
- `sector_report.py`: sector analysis.
- `report_generator.py`, `research_engine.py`, `research_data.py`, `report_charts.py`: PDF research report.
- `chart_integration.py`, `swing_trades.py`, `ai_engine.py`, `market_news.py`: imported by `main.py`; review before removing.
- `nifty500_collector.py`: `SECTOR_STOCKS` imported by `main.py`; also referenced by research pipeline. Preserve manual collection.
- `requirements.txt`, `render.yaml`, `builder.sh`: deployment dependencies; do not delete without build proof.
- `tests/`: preserve regression tests.

## REMOVE — implemented on this review branch
- Five-minute cron trigger in `.github/workflows/msk-codex-autodev.yml`; manual dispatch remains.
- Unused `start_scheduler_thread()` in `nifty500_collector.py`; no call sites found in repository code search. Collection functions remain.

## FUTURE PROJECT / ARCHIVE CANDIDATES — NOT DELETED
- `fundamental_pipeline.py`, `fundamental_job.py`: channel-oriented daily research posting and send tracking. Separate from the interactive bot. Has tests and an internal sector dependency; archive only after full dependency and test review.
- `scripts/msk_pick_task.py`, `docs/MSK_CODEX_AUTOMATION.md`, `.github/workflows/msk-codex-autodev.yml`: automated coding workflow. Keep for possible future development agent; do not run on a schedule.
- `EXPERT_PRODUCT_AUDIT.md`, `CHART_INTEGRATION_GUIDE.md`, `FIXES.md`: reference documents; move to docs/archive after review, not runtime code.
- `test_fundamental_pipeline.py` and `tests/test_fundamental_pipeline.py`: retain until channel module archived; then move with it.

## TRASH — none yet
No whole-file deletion is safe from name alone. Full repository tree, import/dependency checks, test run and Render smoke test are required before permanent deletion.

## Review checklist
1. Confirm bot commands and buttons work unchanged.
2. Run Python compilation and existing tests on this branch.
3. Check Render startup and webhook handling.
4. Merge only after review; no deployment or deletion was performed here.
