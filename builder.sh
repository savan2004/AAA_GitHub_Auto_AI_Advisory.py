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
pip install -r requirements.txt

echo "==> Build completed successfully"
