#!/usr/bin/env bash
# ============================================================================
# UX Analyser — server launcher (macOS / Linux)
#
# Activates the local virtualenv and starts the FastAPI server on port 8000.
# The Chrome extension's `SERVER` constant must point at the same port.
# ============================================================================

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [[ ! -d "$HERE/venv" ]]; then
  echo "Error: venv/ not found in $HERE" >&2
  echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$HERE/venv/bin/activate"

echo "Starting UX Analyser FastAPI server on http://127.0.0.1:8000"
exec uvicorn server:app --reload --port 8000
