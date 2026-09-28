#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SEMANTIC_MODE="${SEMANTIC_MODE:-fake}"
export RAG_MODE="${RAG_MODE:-local}"
export PII_MODE="${PII_MODE:-regex}"
export OTEL_ENABLED="${OTEL_ENABLED:-false}"
export ACCESS_SECRET="${ACCESS_SECRET:-local-development-secret}"
python -m app.eval.run_eval
python scripts/test_memory_persistence.py
python scripts/smoke_mcp.py
python -m pytest -q
python scripts/validate_evidence.py
