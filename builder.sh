#!/usr/bin/env bash
set -euo pipefail

echo "==> Build started in: $(pwd)"
echo "==> Python version: $(python --version 2>/dev/null || true)"
echo "==> Listing project files"
ls -la

if [ ! -f requirements.txt ]; then
  echo "ERROR: requirements.txt not found in $(pwd)"
  exit 1
fi

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

# Render must build with the SAME Python that starts the chart subprocess.
# Reject deployments with a missing transitive dependency before switching
# live Telegram traffic. No external network is used by this import check.
python - <<'PY'
import importlib
import sys
for name in ("six", "numpy", "pandas", "matplotlib", "mplfinance",
             "yfinance", "curl_cffi", "reportlab", "PIL"):
    try:
        importlib.import_module(name)
    except (ImportError, OSError) as exc:
        raise SystemExit("MSK_CHART_DEPENDENCY_CHECK_FAILED module=" + name +
                         " error=" + type(exc).__name__) from exc
import six
print("MSK_CHART_DEPS_OK six=" + six.__version__ +
      " python=" + sys.version.split()[0])
PY

echo "==> Build completed successfully"
