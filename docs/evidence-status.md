# Evidence Status

The corrected repository is intentionally split into two operating modes.

## Local software verification

`./scripts/run_smoke.sh` runs the application with fake semantic inference, local lexical RAG, regex PII handling and no Phoenix dependency. This mode verifies software behavior but does not claim rubric-grade Phoenix evidence.

## Submission evidence generation

`uv run python scripts/generate_evidence.py` is fail-closed. It requires a separate `.venv-phoenix`, a live Phoenix server on `http://127.0.0.1:6006`, Chromium for the UI capture, and real Gemini/Groq credentials for the evaluation path.

The following are generated only by that pipeline:

- `traces/phoenix_spans.jsonl`
- `traces/phoenix_spans.jsonl.meta.json`
- `reports/golden_signals.json`
- `reports/dashboard_data.csv`
- `reports/dashboard_data.csv.meta.json`
- `reports/dashboard.png`
- `reports/dashboard.png.capture-meta.json`
- `reports/eval_report.json`
- `reports/deepeval_qualitative.json`
- `reports/failure-scenarios.json`
- machine-generated tool/audit/memory logs

Stale evidence artifacts from the previous submission are not carried into this corrected tree. The required evidence paths are created only by the current evidence-generation pipeline.
