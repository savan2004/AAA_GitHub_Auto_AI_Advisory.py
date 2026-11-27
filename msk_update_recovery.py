"""Read-only aggregate report for unresolved Telegram updates; never replay trades."""
import argparse
import json
import os

_QUERY = """SELECT stage, COUNT(*), MIN(claimed_at)
FROM msk_bot_state.telegram_updates
WHERE stage IN ('claimed', 'failed')
  AND claimed_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute')
GROUP BY stage ORDER BY stage"""


def recovery_report(dsn, *, minimum_age_minutes=15, connector=None):
    if type(minimum_age_minutes) is not int or not 1 <= minimum_age_minutes <= 525600:
        raise ValueError("Minimum age must be 1..525600 minutes")
    if connector is None:
        from postgres_portfolio import _validate_dsn, _connect_postgres
        dsn = _validate_dsn(dsn)
        connector = _connect_postgres
    with connector(dsn) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(_QUERY, (minimum_age_minutes,)).fetchall()
    stages = {}
    for stage, count, oldest in rows:
        if stage not in ('claimed', 'failed'):
            raise ValueError("Unexpected update stage")
        stages[stage] = {"count": int(count), "oldest_claimed_at": oldest.isoformat() if oldest else None}
    total = sum(item["count"] for item in stages.values())
    return {"status": "review_required" if total else "no_aged_unresolved_updates",
            "minimum_age_minutes": minimum_age_minutes,
            "aged_unresolved_count": total, "stages": stages,
            "automatic_replay_allowed": False,
            "portfolio_execution_verified": False}


def main(argv=None, *, environ=None, reporter=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-age-minutes", type=int, default=15)
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    dsn = env.get("MSK_STATE_DATABASE_URL")
    if not dsn:
        print(json.dumps({"status": "unavailable", "reason": "MSK_STATE_DATABASE_URL is required"}))
        return 2
    try:
        report = (reporter or recovery_report)(dsn, minimum_age_minutes=args.minimum_age_minutes)
    except Exception:
        # Database exceptions may contain credentials: never print exception text.
        print(json.dumps({"status": "unavailable", "reason": "Recovery inspection failed; operator review required"}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["aged_unresolved_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
