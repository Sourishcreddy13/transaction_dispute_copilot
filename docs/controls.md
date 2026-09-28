# Control Catalog

Every `CTRL-*` mitigation cited by the risk register resolves to one committed
implementation and one repository test. This is the executable control map used
by the citation-resolves review.

| Control ID | Implements | Code | Test |
|---|---|---|---|
| `CTRL-GUARD-001` | Prompt-injection detection and quarantine of untrusted customer text | `src/context/engineering.py::ContextEngineer.isolate`; `src/graph.py::CopilotGraph.supervisor` | `tests/test_security.py`, `tests/test_context_engineering.py` |
| `CTRL-PII-001` | PII/PAN masking before persistence and customer-output leak prevention | `app/core/pii.py::PIIService.scan`; `src/guardrails/validators.py::validate_customer_view` | `tests/test_pii.py`, `tests/test_guardrails.py` |
| `CTRL-AUTHZ-001` | Signed access context and customer/case ownership checks at the MCP boundary | `app/core/security.py::mint_context`; `mcp_server/server.py::_ctx` and tool ownership checks | `tests/test_idor.py`, `tests/test_security.py` |
| `CTRL-RAG-001` | Policy retrieval must contain the exact cited source before a policy is considered matched | `app/core/engines.py::PolicyEngine`; `src/tools/rag_tool.py::RAGTool.search` | `tests/test_policy.py`, `tests/test_correctness_regressions.py`, `tests/test_rag.py` |
| `CTRL-PROVIDER-001` | Bounded provider calls, retries and Gemini→Groq fallback | `app/core/semantic.py::SemanticGateway._run`, `RealProvider._invoke` | `tests/test_semantic_budget.py` |
| `CTRL-DECISION-001` | Deterministic fraud/policy/decision boundary; LLM never owns the financial decision | `app/core/engines.py::FraudEngine`, `PolicyEngine`, `DecisionEngine` | `tests/test_policy.py`, `tests/test_correctness_regressions.py` |
| `CTRL-IDEMPOTENCY-001` | Idempotent finalization/outbox behavior | `app/core/db.py::finalize_review`, `app/core/outbox.py` | `tests/test_outbox.py`, `tests/test_full_workflow.py` |
| `CTRL-HITL-001` | Human-review interrupt, persistent checkpoint and reviewer-resume path | `src/graph.py::human_review`; `app/workflow.py::resume_review` | `tests/test_hitl.py`, `tests/test_full_workflow.py` |
| `CTRL-MEMORY-001` | Durable verified customer memory with semantic-overlay rehydration | `src/memory/store.py::TieredMemory` | `tests/test_memory_persistence.py` |
| `CTRL-EVIDENCE-001` | Fail-closed Phoenix export and evidence provenance | `scripts/export_traces.py`, `scripts/generate_evidence.py`, `scripts/validate_evidence.py` | `tests/test_evidence_integrity.py` |
| `CTRL-EVAL-001` | Decision correctness owned by deterministic assertions; LLM judge limited to qualitative metrics | `app/eval/run_eval.py`, `app/eval/deepeval_suite.py` | `tests/test_evaluation_design.py`, `tests/test_deepeval_qualitative_result_keys.py` |
| `CTRL-TRACE-001` | Phoenix/OpenInference tracing with project attribution | `src/observability/tracing.py::TraceManager` | `tests/test_tracing_phoenix_register_workaround.py` |

## Citation rule

When a `CTRL-*` ID is introduced in `docs/risk-register.md`, it must appear in
this table in the same commit and point to an implementing function and a test.
