"""Opt-in GitHub Actions incident collector, using stdlib only.

Requires a short-lived GitHub token supplied through Actions permissions.
Fetches only repository workflow conclusions; never reads secrets or executes fixes.
"""
from __future__ import annotations
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
def fetch_workflow_failures(repo: str, token: str, *, limit: int = 30) -> list[str]:
    if not repo or "/" not in repo or not token:
        raise ValueError("repository and token required")
    owner, name = repo.split("/", 1)
    import re
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in (owner, name)):
        raise ValueError("invalid repository")
    url = f"{API}/repos/{owner}/{name}/actions/runs?per_page={min(max(limit,1),100)}"
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "msk-lite-control/1",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    lines = []
    for run in payload.get("workflow_runs", []):
        if run.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required"):
            name = str(run.get("name") or "workflow").replace("\n", " ")[:90]
            run_id = str(run.get("id") or "unknown")
            lines.append(f"CI failed: {name} run {run_id} conclusion={run['conclusion']}")
    return lines

def main() -> None:
    from tools.msk_repair_control import analyze
    from tools.msk_control_panel import render
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    out = Path(os.environ.get("MSK_CONTROL_OUTPUT", "artifacts/msk-control"))
    out.mkdir(parents=True, exist_ok=True)
    report = analyze(fetch_workflow_failures(repo, token))
    (out / "incidents.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "index.html").write_text(render(report), encoding="utf-8")
    print(f"Collected {report['total_events']} workflow failures (read-only)")

if __name__ == "__main__":
    main()
