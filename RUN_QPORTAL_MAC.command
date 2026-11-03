#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=============================================="
echo " QPortal - clean macOS setup and launch"
echo "=============================================="

# Prevent global Python packages from leaking into this project.
unset PYTHONPATH || true
unset PYTHONHOME || true
export PYTHONNOUSERSITE=1

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "ERROR: Python 3.12 was not found."
  echo "Install it with: brew install python@3.12"
  exit 1
fi

PYTHON="$(command -v python3.12)"
echo "Using Python: $PYTHON"
"$PYTHON" --version

# Always create a clean environment with this exact interpreter.
# (This only ever deletes the project's own .venv folder, nothing else.)
rm -rf "$ROOT/.venv"

# Normal route: a standard venv that already includes pip. Nothing is downloaded for this step.
if "$PYTHON" -m venv "$ROOT/.venv" && "$ROOT/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "pip is available in the new environment."
else
  # Fallback for machines where ensurepip is broken: download pip's official installer (get-pip.py)
  # from bootstrap.pypa.io and run it inside .venv. Only used when the normal route above fails.
  echo "ensurepip is not working here; falling back to pip's official get-pip.py installer."
  rm -rf "$ROOT/.venv"
  "$PYTHON" -m venv --without-pip "$ROOT/.venv"
  GETPIP="$(mktemp -t qportal-get-pip)"
  trap 'rm -f "$GETPIP"' EXIT
  curl -fL https://bootstrap.pypa.io/get-pip.py -o "$GETPIP"
  "$ROOT/.venv/bin/python" "$GETPIP"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ROOT/.venv/bin/python" -m pip install --no-cache-dir -r "$ROOT/portal/requirements.txt"
"$ROOT/.venv/bin/python" -m pip check

echo
echo "Checking numerical and quantum packages..."
"$ROOT/.venv/bin/python" - <<'PY'
import sys
import numpy as np
import pandas as pd
import scipy
import qiskit
import qiskit_aer
import qiskit_ibm_runtime
import qiskit_optimization
import qiskit_algorithms
import fastapi
from importlib.metadata import version

print("Python:", sys.executable)
print("NumPy:", np.__version__)
print("NumPy np.long:", hasattr(np, "long"))
print("NumPy np.ulong:", hasattr(np, "ulong"))
print("Pandas:", pd.__version__)
print("SciPy:", scipy.__version__)
print("Qiskit:", qiskit.__version__)
print("Qiskit Aer:", qiskit_aer.__version__)
print("Qiskit IBM Runtime:", version("qiskit-ibm-runtime"))
print("Qiskit Optimization:", version("qiskit-optimization"))
print("FastAPI:", fastapi.__version__)

assert hasattr(np, "long") and hasattr(np, "ulong"), "NumPy compatibility aliases are missing"
import portal.analytics
import portal.main
print("QPortal imports: OK")
PY

echo
echo "Starting QPortal at http://127.0.0.1:8000"
echo "Press Ctrl+C to stop the server."
exec "$ROOT/.venv/bin/python" -m uvicorn portal.main:app --host 127.0.0.1 --port 8000
