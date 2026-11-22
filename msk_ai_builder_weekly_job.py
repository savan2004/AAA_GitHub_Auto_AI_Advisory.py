"""Opt-in weekly MSK research staging job -- never customer publication."""
import json
import os
from collections import Counter

from msk_ai_builder_bank import BankError, build_weekly_bank
from msk_ai_builder_weekly import stage_week


def run_weekly_job(*, environ=None, bank_factory=build_weekly_bank, refresh=stage_week):
    env = os.environ if environ is None else environ
    flag = env.get("MSK_BUILDER_WEEKLY_JOB_ENABLED", "0")
    if flag == "0":
        return {"status": "DISABLED", "publication_status": "WITHHELD"}
    if flag != "1":
        raise BankError("INVALID_WEEKLY_JOB_FLAG")
    bank = bank_factory(environ=env)
    if bank is None:
        raise BankError("WEEKLY_BANK_DISABLED")
    result, version = refresh(bank, once_per_week=True)
    return {
        "status": result.status,
        "publication_status": "WITHHELD",
        "attempted": len(result.attempted),
        "selected": len(result.companies),
        "failure_counts": dict(Counter(result.failures.values())),
        "version": version,
    }


def main():
    try:
        summary = run_weekly_job()
    except Exception:
        # Connection/provider exceptions may contain private connection details.
        print(json.dumps({"status": "REFRESH_FAILED", "publication_status": "WITHHELD"}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] in (
        "DISABLED", "SKIPPED_ALREADY_REVIEWED", "INTERNAL_SECONDARY_STAGING"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
