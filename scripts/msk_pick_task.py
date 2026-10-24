"""Zero-cost task selector; no API calls to paid AI services."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone


def gh(*args):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def _covered_by_open_pr(issue_number, prs):
    """Avoid repeated AI work when an existing open PR already covers the issue."""
    issue_title = re.compile(rf"\bissue\s*#\s*{issue_number}\b", re.IGNORECASE)
    closing_ref = re.compile(
        rf"\b(?:closes|fixes|resolves|implements|for)\s+(?:issue\s*)?#\s*{issue_number}\b",
        re.IGNORECASE,
    )
    for pr in prs:
        if pr.get("state", "OPEN").upper() != "OPEN":
            continue
        if issue_title.search(pr.get("title") or ""):
            return True
        if closing_ref.search(pr.get("body") or ""):
            return True
    return False


def choose(environ, now, issues, prs):
    if environ.get("ENABLED", "").lower() != "true" or environ.get("ACK_COSTS", "").lower() != "true":
        return None
    if environ.get("API_KEY_SET") != "true":
        return None
    # Strict cost ceiling: at most ONE scheduled paid attempt per UTC day.
    # The 5-minute job only polls for tasks; execution is once per day.
    if environ.get("EVENT") == "schedule" and (now.hour != 0 or now.minute >= 5):
        return None
    for issue in sorted(issues, key=lambda i: i["number"]):
        labels = {x["name"] for x in issue.get("labels", [])}
        if {"msk-codex-approved", "msk-codex-ready"} - labels:
            continue
        if "msk-codex-blocked" in labels:
            continue
        if _covered_by_open_pr(issue["number"], prs):
            continue
        return issue["number"]
    return None


def main():
    out = os.environ["GITHUB_OUTPUT"]
    env = {
        "ENABLED": os.getenv("ENABLED", ""),
        "ACK_COSTS": os.getenv("ACK_COSTS", ""),
        "API_KEY_SET": os.getenv("API_KEY_SET", ""),
        "EVENT": os.getenv("EVENT", "")
    }
    issue = None
    if all(env[k] == "true" for k in ("ENABLED", "ACK_COSTS", "API_KEY_SET")):
        issues = gh("issue", "list", "--state", "open", "--limit", "100", "--json", "number,labels")
        prs = gh("pr", "list", "--state", "open", "--limit", "100", "--json", "title,body,state")
        issue = choose(env, datetime.now(timezone.utc), issues, prs)
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"execute={'true' if issue else 'false'}\n")
        if issue:
            f.write(f"issue={issue}\n")
    print("Approved development task selected." if issue else "No eligible paid work; scheduler exit.")


if __name__ == "__main__":
    main()
