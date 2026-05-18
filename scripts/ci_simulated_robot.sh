#!/usr/bin/env bash
# Local equivalent of .github/workflows/simulated-robot.yml
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
pip install -e ".[dev,media,app]"

echo "=== unittest discover ==="
python -m unittest discover -s tests -v

echo "=== smoke_sim_runtime ==="
PYTHONPATH=src python tests/smoke_sim_runtime.py

echo "CI simulated-robot checks passed."
