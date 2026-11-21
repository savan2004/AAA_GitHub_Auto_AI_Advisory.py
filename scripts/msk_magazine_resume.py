"""Checkpoint one supplied Design 3 draft page per IST day in a draft release."""
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

from scripts.msk_magazine_monthly import IST, UNAPPROVED, first_monday, load_master


def next_page(assets, today):
    """The immutable asset names and GitHub byte digests form the progress record."""
    dates = {}
    for asset in assets:
        match = re.fullmatch(r"page_(\d{2})_(\d{4}-\d{2}-\d{2})_([a-f0-9]{64})\.pdf", asset["name"])
        if not match or asset.get("digest") != "sha256:" + match[3]:
            raise ValueError("Unexpected asset or mismatched PDF checksum")
        page, day = int(match[1]), date.fromisoformat(match[2])
        if page in dates or not first_monday(today.year, today.month) <= day <= today:
            raise ValueError("Invalid page checkpoint")
        dates[page] = day
    ordered = [dates[n] for n in sorted(dates)]
    if (len(dates) > 16 or sorted(dates) != list(range(1, len(dates) + 1))
            or ordered != sorted(set(ordered))):
        raise ValueError("Invalid checkpoint sequence")
    if len(dates) < 16 and today not in ordered:
        return len(dates) + 1


def gh(*args):
    return subprocess.run(["gh", *args], text=True, capture_output=True)


def advance(today, root, run=gh):
    issue = today.strftime("%Y-%m")
    start = first_monday(today.year, today.month)
    if today < start:
        return "Before edition start"
    tag = f"msk-magazine-{issue}-page-progress"
    view = run("release", "view", tag, "--json", "isDraft,assets")
    if view.returncode:
        if view.stderr.strip().lower() != "release not found":
            raise RuntimeError(view.stderr)
        if today != start:
            return "No current-month checkpoint to resume"
        run("release", "create", tag, "--draft", "--title", f"MSK {issue} draft page progress",
            "--notes", "Unapproved page checkpoints for editorial review.").check_returncode()
        assets = []
    else:
        release = json.loads(view.stdout)
        if release["isDraft"] is not True:
            raise ValueError("Page checkpoint release must be a draft")
        assets = release["assets"]
    inputs = {a["name"]: a for a in assets
              if re.fullmatch(r"page_(0[1-9]|1[0-6])\.pdf", a["name"])}
    page = next_page([a for a in assets if a["name"] not in inputs], today)
    if page is None:
        return "Already checkpointed today or all 16 drafts staged"
    candidate = root / issue / f"page_{page:02d}.pdf"
    if candidate.name not in inputs:
        return f"Awaiting authored draft {candidate.name} on draft release {tag}"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    run("release", "download", tag, "--pattern", candidate.name,
        "--dir", str(candidate.parent)).check_returncode()
    data = candidate.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if inputs[candidate.name].get("digest") != "sha256:" + digest:
        raise ValueError("Downloaded page differs from recorded upload digest")
    pdf = PdfReader(io.BytesIO(data))
    if len(pdf.pages) != 1:
        raise ValueError("Expected one draft page")
    sheet = pdf.pages[0]
    if any(abs(float(size) - target) > 1 for size, target in
           zip((sheet.mediabox.width, sheet.mediabox.height), A4)):
        raise ValueError("Expected A4 portrait")
    content = " ".join((sheet.extract_text() or "").split()).casefold()
    title = load_master()["locked_index"][page - 1]["title"]
    for required in (issue, title, UNAPPROVED, f"{page:02d}/16"):
        if required.casefold() not in content:
            raise ValueError(f"Missing expected page text: {required}")
    with tempfile.TemporaryDirectory() as folder:
        asset = Path(folder) / f"page_{page:02d}_{today.isoformat()}_{digest}.pdf"
        asset.write_bytes(data)
        run("release", "upload", tag, str(asset)).check_returncode()
    return f"Staged unapproved page {page:02d}, SHA-256 {digest}"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as work:
        print(advance(datetime.now(IST).date(), Path(work)))
