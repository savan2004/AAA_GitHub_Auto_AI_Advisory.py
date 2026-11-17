"""Private Portfolio Doctor worker: bounded execution, no saved-holdings access.

The parent process receives only a validated PDF path. The child runs inside a
private temp workspace and is terminated on deadline. Nothing sends Telegram
from this module.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

MAX_BYTES = 5 * 1024 * 1024
MAX_STOCKS = 5
DEFAULT_TIMEOUT = 150


class PortfolioDoctorError(RuntimeError):
    def __init__(self, message, code="UNKNOWN"):
        super().__init__(message)
        self.code = code


class PortfolioDoctorTimeout(PortfolioDoctorError):
    pass


def _pdf_in_workspace(path, workspace):
    root = Path(workspace).resolve(strict=True)
    pdf = Path(path).resolve(strict=True)
    if pdf.parent != root or pdf.suffix.lower() != ".pdf":
        raise PortfolioDoctorError("Report escaped private workspace")
    if pdf.stat().st_size < 1000:
        raise PortfolioDoctorError("PDF integrity check failed")
    with pdf.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise PortfolioDoctorError("PDF signature invalid")
    return pdf


def run_isolated(filename, payload, workspace, timeout=DEFAULT_TIMEOUT):
    """Run private parse + full report in a killable child process."""
    if not filename.lower().endswith((".csv", ".xlsx")):
        raise PortfolioDoctorError("Unsupported file type")
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_BYTES:
        raise PortfolioDoctorError("Invalid upload size")
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 240:
        raise ValueError("Timeout must be between 0 and 240 seconds")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise PortfolioDoctorError("Private workspace is missing")
    upload = root / "upload.bin"
    result = root / "result.json"
    fd = os.open(str(upload), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(payload)
    env = os.environ.copy()
    temp_root = root / "tmp"
    temp_root.mkdir(mode=0o700)
    for key in ("TMPDIR", "TEMP", "TMP", "MPLCONFIGDIR", "XDG_CACHE_HOME", "MSK_REPORT_CHART_DIR"):
        env[key] = str(temp_root)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker",
               str(upload), str(result), str(root), filename]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env,
                               cwd=str(Path(__file__).resolve().parent),
                               start_new_session=(os.name == "posix"))
    try:
        status = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
        raise PortfolioDoctorTimeout("Private report exceeded hard deadline", code="TIMEOUT") from exc
    if status != 0 or not result.is_file():
        code = "WORKER_FAILED"
        if result.is_file():
            try:
                failure = json.loads(result.read_text(encoding="utf-8"))
                allowed = {"INPUT_INVALID", "NO_RECENT_PRICES", "STALE_OR_MISSING_PRICES",
                           "PDF_INVALID", "ANALYSIS_FAILED", "WORKER_FAILED"}
                if failure.get("status") == "FAILED" and failure.get("code") in allowed:
                    code = failure["code"]
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        raise PortfolioDoctorError("Private analysis failed or incomplete", code=code)
    try:
        result_data = json.loads(result.read_text(encoding="utf-8"))
        if result_data.get("status") != "READY":
            raise PortfolioDoctorError("Private report not ready")
        return _pdf_in_workspace(result_data["summary"], root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PortfolioDoctorError("Invalid private worker receipt") from exc


def _worker(upload, result, workspace, filename):
    root = Path(workspace).resolve(strict=True)
    # The parent subprocess environment already sets TMPDIR/TEMP/TMP to the
    # private workspace. Do not mutate process-global tempfile.tempdir.
    from portfolio_report import parse_file, run_job
    holdings, issues = parse_file(filename, Path(upload).read_bytes())
    if (not holdings or len(holdings) > MAX_STOCKS or
            any("duplicate symbol" not in issue for issue in issues)):
        raise PortfolioDoctorError("Portfolio upload is invalid", code="INPUT_INVALID")
    output = run_job(holdings, str(root), max_analyze=MAX_STOCKS, use_ai=False)
    recs = output.get("recs", [])
    # A private portfolio summary is useful with fresh, verified prices even
    # when optional per-stock research PDFs cannot be generated. The summary's
    # own disclosures flag any unscored positions; never fake a technical score.
    if (len(recs) != len(holdings) or
            any(not r.get("priced") or r.get("freshness") != "Fresh" for r in recs)):
        raise PortfolioDoctorError("Incomplete, stale or unverified market report",
                                   code="STALE_OR_MISSING_PRICES")
    try:
        pdf = _pdf_in_workspace(output["summary"], root)
    except (KeyError, OSError, PortfolioDoctorError) as exc:
        raise PortfolioDoctorError("Private summary PDF validation failed",
                                   code="PDF_INVALID") from exc
    Path(result).write_text(json.dumps({"status": "READY", "summary": str(pdf)}),
                            encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[1] != "--worker":
        sys.exit(2)
    try:
        _worker(*sys.argv[2:])
    except Exception as exc:
        # Only bounded, non-private error codes reach the parent. Never emit
        # symbols, quantities, purchase prices, raw exception text or PDF paths.
        code = "ANALYSIS_FAILED"
        if isinstance(exc, PortfolioDoctorError):
            code = exc.code if exc.code != "UNKNOWN" else "ANALYSIS_FAILED"
        elif isinstance(exc, ValueError) and str(exc).startswith(
                "None of the symbols returned price data."):
            code = "NO_RECENT_PRICES"
        try:
            Path(sys.argv[3]).write_text(json.dumps({"status": "FAILED", "code": code}),
                                         encoding="utf-8")
        except OSError:
            pass
        sys.exit(1)
