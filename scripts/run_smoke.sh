#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SEMANTIC_MODE="fake"
export RAG_MODE="local"
export PII_MODE="regex"
export OTEL_ENABLED="false"
export ACCESS_SECRET="${ACCESS_SECRET:-local-development-secret}"
uv run python scripts/seed_data.py
uv run python scripts/smoke_mcp.py
uv run python scripts/test_memory_persistence.py
uv run python -m app.eval.run_eval
uv run python -m pytest -q
uv run python scripts/validate_evidence.py
