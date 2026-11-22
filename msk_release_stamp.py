"""Stable build metadata for the currently running MSK Bot, not a live clock."""
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timedelta, timezone

STAMP = Path(__file__).resolve().with_name(".msk_build_info.json")
IST = timezone(timedelta(hours=5, minutes=30))
SHA = re.compile(r"[0-9a-f]{40}")


def write_build_stamp(path=STAMP, *, environ=None, now=None):
    env = os.environ if environ is None else environ
    commit = env.get("RENDER_GIT_COMMIT", "")
    if not SHA.fullmatch(commit):
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    stamp = now or datetime.now(timezone.utc)
    if stamp.utcoffset() is None:
        raise ValueError("Build timestamp must include timezone")
    payload = {"commit": commit if SHA.fullmatch(commit) else None,
               "built_at": stamp.astimezone(timezone.utc).isoformat()}
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    temp.replace(path)


def release_banner(path=STAMP, *, environ=None):
    env = os.environ if environ is None else environ
    try:
        with Path(path).open(encoding="utf-8") as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            raise ValueError
        data = json.loads(raw)
        commit = data["commit"]
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError
        running = env.get("RENDER_GIT_COMMIT")
        if running is not None and running != commit:
            raise ValueError
        stamp = datetime.fromisoformat(data["built_at"])
        if stamp.utcoffset() is None:
            raise ValueError
        local = stamp.astimezone(IST)
        return (f"🆕 <b>Version:</b> {local:%Y.%m.%d} · {commit[:7]}\n"
                f"🕒 <b>Build updated:</b> {local:%d %b %Y, %I:%M %p} IST")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return "🆕 Version timestamp unavailable"


if __name__ == "__main__":
    write_build_stamp()
