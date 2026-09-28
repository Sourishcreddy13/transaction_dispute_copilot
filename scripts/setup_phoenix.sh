#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3.12 -m venv .venv-phoenix
.venv-phoenix/bin/pip install --upgrade pip
.venv-phoenix/bin/pip install arize-phoenix
printf '\nPhoenix environment ready. Start it with:\n  .venv-phoenix/bin/phoenix serve\n'
