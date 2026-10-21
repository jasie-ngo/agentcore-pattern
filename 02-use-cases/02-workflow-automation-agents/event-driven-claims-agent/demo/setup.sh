#!/usr/bin/env bash
# Build the demo environment from the locked, hash-checked requirements. Needs `uv` and network access (once).
#   bash demo/setup.sh            creates demo/.venv
#   demo/.venv/bin/python demo/run_demo.py
set -euo pipefail
cd "$(dirname "$0")"
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
echo "ready: demo/.venv/bin/python demo/run_demo.py --fast"
