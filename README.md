# Transaction Dispute & Fraud-Triage Copilot — Corrected Runnable Version

This repository is a corrected implementation of the supplied hackathon specification. It keeps the original LangGraph + MCP + RAG + memory + governance architecture and fixes the correctness, provenance, and reproducibility defects identified during review.

## Architecture

```text
Customer dispute
      |
      v
 PII scan/mask + injection detection
      |
      v
   LangGraph supervisor
      |
      +--> classification agent ---- Gemini -> Groq fallback
      +--> case-data agent ---------- MCP tools
      +--> fraud-scoring agent ------ deterministic features
      +--> chargeback agent --------- MCP resource + Agentic RAG
      |
      v
 deterministic policy + decision engine
      |
      v
 invariant/release gate
      |
      +--> automatic outcome
      |
      +--> human review interrupt --> checkpoint --> reviewer resume
      |
      v
 audit trail + outbox + customer-safe response
```

The LLM is used for semantic interpretation and bounded query rewriting. Financial eligibility, fraud scoring, action selection, escalation and release invariants remain deterministic.

## 1. Install

Python 3.12 is the tested target for this repository.

```bash
uv sync --extra dev
```

or:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 2. Local deterministic smoke run

This mode needs no API keys and does not pretend to create Phoenix evidence. It is for checking the application, MCP boundary, RAG, memory and tests.

```bash
./scripts/local_run.sh
```

The script sets:

```text
SEMANTIC_MODE=fake
RAG_MODE=local
PII_MODE=regex
OTEL_ENABLED=false
```

## 3. Run a real dispute

Create `.env` from `.env.example` and provide the Gemini primary key plus the Groq fallback key.

```bash
cp .env.example .env
```

Then:

```bash
uv run python scripts/seed_data.py
uv run python -m app.cli open-case --customer C-1001 --transaction T-1007
uv run python -m app.cli run --case <CASE_ID> --input samples/dispute.json
```

The output is a customer-safe `RunResponse` containing classification, fraud assessment, policy evidence, recommendation, audit/provenance information and review state.

## 4. Human-review flow

High-risk/high-value cases stop at a LangGraph `interrupt()` and are checkpointed in SQLite.

After the run returns a review task:

```bash
uv run python -m app.cli review \
  --task <TASK_ID> \
  --version <VERSION> \
  --action investigate \
  --reason-code HIGH_RISK \
  --note "manual review"
```

The CLI first claims/resolves the task and then calls the checkpointed graph resume path. The resumed graph reaches `disposition_commit` and then `finalize`.

## 5. Phoenix evidence run

Phoenix evidence is intentionally fail-closed. A local diagnostic span file is never copied into `traces/phoenix_spans.jsonl`, and a locally-rendered chart is never accepted as the required Phoenix screenshot.

The application environment and the Phoenix UI/query client use separate virtual environments because the current full Phoenix package has dependency conflicts with the project's MCP client stack.

Create the separate Phoenix environment:

```bash
python3.12 -m venv .venv-phoenix
.venv-phoenix/bin/pip install arize-phoenix
```

Start Phoenix in a separate terminal:

```bash
.venv-phoenix/bin/phoenix serve
```

Install browser support in the project environment:

```bash
uv run playwright install chromium
```

Then generate all evidence:

```bash
uv run python scripts/generate_evidence.py
```

That pipeline requires:

```text
.venv-phoenix/bin/python
live Phoenix at http://127.0.0.1:6006
Chromium for the dashboard screenshot
Gemini/Groq credentials for the real pipeline/evaluation
```

It generates:

```text
traces/phoenix_spans.jsonl
reports/golden_signals.json
reports/dashboard_data.csv
reports/dashboard.png
reports/eval_report.json
reports/deepeval_qualitative.json
reports/failure-scenarios.json
logs/tool_calls.jsonl
logs/agent_actions.jsonl
logs/memory_test.log
logs/mcp_transcript.jsonl
```

`reports/dashboard.png` is produced only by `scripts/capture_phoenix_dashboard.py` and is never replaced with a Matplotlib fallback.

## 6. Useful commands

```bash
make test
make lint
make format-check
make typecheck
make demo
make eval
make deep-eval
make evidence
```

For a supplemental local chart based on the exported Phoenix spans:

```bash
uv run python scripts/build_dashboard.py
```

This writes `reports/dashboard_summary.png`. It is intentionally not the required Phoenix dashboard artifact.

## 7. Evidence integrity

The corrected repository treats evidence as a data-lineage problem:

```text
Phoenix collector
      |
      v
traces/phoenix_spans.jsonl
      |
      +--> reports/golden_signals.json
      +--> reports/dashboard_data.csv
      +--> failure-analysis references
```

No generated artifact is allowed to claim a Phoenix origin when it came from local fallback telemetry.

Policy citations also fail closed: a deterministic policy rule is only marked `matched=True` when the corresponding source is present in the actual RAG retrieval result.

## 8. Key correctness fixes

The corrected version includes, among others:

- actual `AgenticPolicyRAG` consumption from the LangGraph chargeback-rule worker;
- strict RAG-to-policy citation matching;
- 15-minute velocity incorporated into fraud scoring;
- true 30-day behavioral statistics rather than a 90-day history mislabeled as 30-day;
- run-scoped provider telemetry instead of shared mutable `last_*` provenance;
- hard MCP resource timeouts;
- project-root-safe path handling;
- `Settings` environment variables evaluated when the settings object is constructed;
- mandatory SQLite checkpointer for the graded workflow;
- human-review graph resume after reviewer resolution;
- reviewer attribution on finalization audit events;
- fail-closed Phoenix trace export and dashboard capture;
- stronger MCP tool-contract and regression tests.

## 9. Important distinction

`./scripts/local_run.sh` validates the software locally without claiming rubric-grade Phoenix evidence.

`scripts/generate_evidence.py` is the submission-grade evidence pipeline and intentionally fails when the real Phoenix environment, server or screenshot prerequisites are missing.
