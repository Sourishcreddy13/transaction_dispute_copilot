# Requirements Traceability

This document maps the assessment specification to the corrected implementation.
A status of **Implemented** means the code path exists and is covered by tests.
A status of **Generated on run** means the committed code generates the required
runtime evidence.

| Requirement | Implementation | Verification | Status |
|---|---|---|---|
| AC-01 | `src/graph.py`, `src/agents/workers.py`, `src/tools/rag_tool.py`, `app/core/engines.py` | policy gate requires the exact cited rule to appear in actual RAG hits | Implemented |
| AC-02 | `app/core/engines.py`, `config/fraud.yaml` | deterministic tests cover fraud factors including 15-minute velocity | Implemented |
| AC-03 | `config/decision_policy.yaml`, `config/invariants.yaml`, `src/graph.py` | decision/release tests and checkpointed HITL path | Implemented |
| AC-04 | `src/graph.py`, `src/agents/workers.py` | ambiguous/out-of-scope requests do not reach a fabricated decision | Implemented |
| AC-05 | `src/context/`, `src/memory/store.py`, checkpointing | cross-session persistence test; durable SQLite is authoritative | Implemented |
| AC-06 | `app/core/pii.py`, `app/core/security.py`, `src/context/`, `src/guardrails/` | PII/IDOR/injection regression tests | Implemented |
| AC-07 | MCP logging middleware and wrappers | `logs/tool_calls.jsonl`, `logs/mcp_transcript.jsonl` | Generated on run |
| AC-08 | `scripts/run_failure_scenarios.py` | each failure cites Phoenix trace identity or an exact tool-log record | Generated on run |
| AC-09 | `scripts/export_traces.py`, `scripts/build_golden_signals.py`, `scripts/build_dashboard.py`, `scripts/capture_phoenix_dashboard.py` | Phoenix export scoped to evidence window; CSV and golden signals share Phoenix source; screenshot is captured from the Phoenix project page | Generated on run |
| AC-10 | `src/guardrails/validators.py`, `app/core/db.py` | audit/evidence tests | Implemented / Generated on run |
| AC-11 | `docs/risk-register.md`, `docs/model-card.md`, `docs/compliance.md`, `docs/output-risk.md`, `docs/controls.md` | every risk mitigation resolves to a `CTRL-*` control | Implemented |
| AC-12 | `app/eval/`, `tests/` | deterministic scoring plus qualitative DeepEval; skipped qualitative cases remain explicitly counted | Implemented / Generated on run |
| NFR-01 | `.env.example`, `.gitignore`, settings | repository scan | Implemented |
| NFR-02 | `README.md`, `scripts/generate_evidence.py` | reproducible run with fresh evidence window | Implemented |
| NFR-03 | `src/context/engineering.py`, input guardrails | injection/quarantine tests | Implemented |
| NFR-04 | async graph/MCP calls, provider timeout/retry/fallback | semantic/MCP/loop tests | Implemented |
| NFR-05 | synthetic data, masking, PII controls | PII tests and strict validator | Implemented |
| NFR-06 | machine-generated artifacts + strict validator | `scripts/validate_evidence.py --strict` | Implemented |

## Evidence integrity controls

1. `traces/phoenix_spans.jsonl` is produced only by querying Phoenix; there is no runtime-log fallback.
2. Phoenix export is restricted to `EVIDENCE_START_UTC`, preventing historical traces from contaminating the current evidence run.
3. `reports/dashboard_data.csv` is produced directly from the Phoenix export and carries a source SHA-256.
4. `reports/dashboard.png` is produced only by the live Phoenix UI capture and is required to be a project-page capture.
5. Policy citation matching is fail-closed: a configured rule is not considered matched unless its exact source appears in the actual retrieval result set.
6. Provider/cost metrics are provider-specific and the provider-attempt log is cleared before each evidence run.
7. Qualitative evaluation reports expose scored and skipped case counts rather than hiding skipped cases from the denominator.
8. Every failure report contains a resolvable Phoenix span identity or an exact tool-log line.
9. Every `CTRL-*` mitigation in the risk register resolves to a control row with an implementation and test.
