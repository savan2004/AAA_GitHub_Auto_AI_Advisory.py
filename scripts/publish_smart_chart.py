"""Generate a real chart in GitHub CI, then verify its Neon save/read-back.

Never sends Telegram messages or touches portfolios.
"""
import hashlib
from pathlib import Path
import subprocess
import sys

from market_breadth import expected_completed_session
from smart_chart_snapshot import (
    _validate, save_verified_daily_chart, load_verified_daily_chart,
)


def main():
    session = str(expected_completed_session())
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-u", "gen_smart_stock_chart.py"],
        cwd=root, capture_output=True, text=True, timeout=70
    )
    if result.returncode != 0:
        errors = [line for line in (result.stdout + "\n" + result.stderr).splitlines()
                  if line.startswith("[ERROR]") or "Traceback" in line]
        raise RuntimeError("Real chart generation failed: " + "; ".join(errors[-2:])[:250])
    output_lines = [line.removeprefix("OUTPUT:").strip()
                    for line in result.stdout.splitlines() if line.startswith("OUTPUT:")]
    meta_lines = [line.removeprefix("META:").strip()
                  for line in result.stdout.splitlines() if line.startswith("META:")]
    if len(output_lines) != 1 or len(meta_lines) != 1:
        raise RuntimeError("Real chart generator did not return one PNG and one META record")
    png_path = (root / output_lines[0]).resolve()
    expected_dir = (root / "output").resolve()
    if png_path.parent != expected_dir:
        raise RuntimeError("Unexpected chart output path")
    image = png_path.read_bytes()
    meta = meta_lines[0]
    _validate(image, meta, session)
    saved_sha = save_verified_daily_chart(session, image, meta)
    restored = load_verified_daily_chart(session, output_dir=root / "output")
    if restored is None:
        raise RuntimeError("Chart NOT READY: verified Neon read-back missing")
    _, restored_path = restored
    actual_sha = hashlib.sha256(Path(restored_path).read_bytes()).hexdigest()
    if actual_sha != saved_sha:
        raise RuntimeError("Chart NOT READY: Neon PNG byte-for-byte readback mismatch")
    print(
        f"MSK_SMART_CHART_READY session={session} bytes={len(image)} "
        f"sha256={saved_sha[:12]} verified_readback=true"
    )


if __name__ == "__main__":
    main()
