# Requirements Traceability — Transaction Dispute & Fraud-Triage Copilot

This file maps the assessment requirements to implementation artifacts. The requirements specification states that only committed, reproducible evidence is scored and lists the exact artifact paths. See `requirements_and_artifacts_specification.md` in the source package.

| Requirement | Implementation | Test / evidence | Status |
|---|---|---|---|
| AC-01 | `src/graph.py`, `src/agents/workers.py`, `src/tools/rag_tool.py` | `tests/test_routing.py`, `scripts/generate_evidence.py` | IMPLEMENTED |
| AC-02 | `app/core/engines.py` | `tests/test_policy.py` | IMPLEMENTED |
| AC-03 | `app/core/engines.py` + `config/decision_policy.yaml` | `tests/test_policy.py` | IMPLEMENTED |
| AC-04 | `src/agents/workers.py`, `src/graph.py` | `tests/test_routing.py` | IMPLEMENTED |
| AC-05 | `src/context/engineering.py`, checkpoint, `src/memory/store.py` | `tests/test_memory_persistence.py` | IMPLEMENTED |
| AC-06 | `app/core/pii.py`, `src/context/engineering.py`, `app/core/security.py`, `src/guardrails/validators.py` | `tests/test_pii.py`, `tests/test_idor.py` | IMPLEMENTED |
| AC-07 | `mcp_server/server.py` | `logs/tool_calls.jsonl`, `logs/mcp_transcript.jsonl` | GENERATED ON RUN |
| AC-08 | `scripts/run_failure_scenarios.py` | `docs/failure-analysis.md` | GENERATED FROM REAL RUNS |
| AC-09 | `src/observability/tracing.py` (real Phoenix OTLP registration, with a verified workaround for a real `arize-phoenix-otel==0.17.1` upstream bug — see README "Observability" bug 16b), `scripts/export_traces.py` (current `phoenix.client.Client` API, run via `.venv-phoenix` — bug 16a), `scripts/build_dashboard.py` | `traces/`, `reports/golden_signals.json`, `reports/dashboard.png`, `reports/dashboard_data.csv` (dashboard.png captured from live Phoenix UI), `tests/test_tracing_phoenix_register_workaround.py` | GENERATED ON RUN |
| AC-10 | `src/guardrails/validators.py`, audit middleware in `app/core/db.py` | `logs/agent_actions.jsonl`, `tests/test_guardrails.py` | IMPLEMENTED |
| AC-11 | `docs/risk-register.md`, `docs/model-card.md`, `docs/compliance.md`, `docs/output-risk.md` | control IDs linked from docs | IMPLEMENTED |
| AC-12 | `app/eval/deepeval_suite.py` (LLM-judge, with Gemini→Groq fallback per D-01 — see `GeminiWithGroqFallback`, whose Groq call uses `method="json_schema"`, verified against the live Groq API — README bug 17; `run_qualitative_eval` keys its per-metric results by `metric.__name__`, not the nonexistent `.name` — README bug 18; `run_qualitative_eval` skips, rather than crashes on, a case with no customer-facing output yet (e.g. pending human review) instead of handing DeepEval an empty `actual_output` — README bug 21), `app/eval/run_eval.py` | `reports/eval_report.json`, `reports/deepeval_qualitative.json`, `tests/test_routing.py`, `tests/test_loops.py`, `tests/test_tool_contracts.py`, `tests/test_deepeval_judge_fallback.py` (judge fails over to Groq on Gemini quota/rate-limit/5xx, not on unrelated errors, and asserts `method="json_schema"` is used), `tests/test_deepeval_qualitative_result_keys.py` (asserts `run_qualitative_eval` builds its result dict from `metric.__name__` and skips cases with empty customer-facing output) | IMPLEMENTED / GENERATED |
| NFR-01 | `.env.example`, `.gitignore`, `pyproject.toml` | `scripts/validate_evidence.py` | IMPLEMENTED |
| NFR-02 | `README.md`, `Makefile`, `scripts/generate_evidence.py`, `tests/conftest.py` (forces `OTEL_ENABLED=false`/`RAG_MODE=local`/`SEMANTIC_MODE=fake`/`PII_MODE=regex` before its own `Settings` import, so `pytest` no longer silently depends on the developer's real ambient `.env` — README bug 19, found and verified against a real `.env` matching the documented "Environment" section) | reproducible run; full suite verified passing both from a clean shell and under the developer's real ambient env vars (`OTEL_ENABLED=true SEMANTIC_MODE=real RAG_MODE=chroma PII_MODE=presidio`) | IMPLEMENTED |
| NFR-03 | `src/context/engineering.py`, PII guardrail | `tests/test_pii.py`, `tests/test_guardrails.py` | IMPLEMENTED |
| NFR-04 | async graph, `asyncio.to_thread`, MCP async adapter (`asyncio.wait_for` timeout in `src/mcp_client.py`), failure taxonomy, `app/core/semantic.py` (`RealProvider._invoke` uses `method="json_schema"` for Groq's structured output — see README bug 17, verified against the live Groq API; `langchain_groq`'s default method fails deterministically for this project's own rewrite() prompt shape) | `tests/test_semantic_budget.py` (model-provider retry/fallback/budget), `tests/test_mcp_timeout.py` (MCP tool-timeout degrades to `NEEDS_INFO`/`TOOL_TIMEOUT`, both the "unknown transaction" and "known transaction" paths) | IMPLEMENTED |
| NFR-05 | synthetic data only; PII masking | `tests/test_pii.py`, `scripts/validate_evidence.py` | IMPLEMENTED |
| NFR-06 | evidence-generating scripts under version control | evidence lock/validator | IMPLEMENTED |

## Deliberate scope notes

- The mandatory real fallback policy is modeled as **Gemini primary → Groq fallback** because the project owner changed the assessment rule after the original PRD. `docs/deviations.md` records this.
- Frontend visual polish is not an assessment requirement, but the project implements a banking-style analyst workspace because it improves usability.
- Cloud/container deployment remains out of scope for this cut.
