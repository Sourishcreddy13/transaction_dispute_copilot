# Risk Register

Every mitigation cites a committed `CTRL-*` control from `docs/controls.md`.

| ID | Risk | Category | Likelihood | Impact | Mitigation | Residual | Owner |
|---|---|---|---|---|---|---|---|
| RISK-001 | Prompt injection in claimant text | OWASP LLM01 | High | High | [`CTRL-GUARD-001`](controls.md), trust-zone isolation, no tools for the LLM | Medium | AI Engineering |
| RISK-002 | Sensitive data disclosure | OWASP LLM02 | Medium | High | [`CTRL-PII-001`](controls.md), sink allow-lists, customer-safe renderer | Low | Security |
| RISK-003 | Unauthorized customer access | OWASP / IAM | Medium | Critical | [`CTRL-AUTHZ-001`](controls.md), signed access context and case ownership checks | Low | Platform |
| RISK-004 | RAG corpus poisoning | OWASP LLM08/LLM03 | Medium | High | [`CTRL-RAG-001`](controls.md), manifest/hash verification and instruction/data separation | Low | AI Engineering |
| RISK-005 | Provider failure | Reliability | Medium | High | [`CTRL-PROVIDER-001`](controls.md), bounded timeout/retry and Gemini→Groq fallback | Low | Platform |
| RISK-006 | LLM overreach into financial decisions | Agency | Medium | Critical | [`CTRL-DECISION-001`](controls.md), deterministic fraud/policy/decision engines | Low | AI Engineering |
| RISK-007 | Duplicate side effects | Reliability | Medium | High | [`CTRL-IDEMPOTENCY-001`](controls.md), idempotency ledger and outbox | Low | Platform |
| RISK-008 | Reviewer race / stale decision | Workflow | Medium | High | [`CTRL-HITL-001`](controls.md), checkpointed reviewer resume and compare-and-set versioning | Low | Platform |
| RISK-009 | Incorrect policy recommendation | Domain correctness | Medium | Critical | [`CTRL-RAG-001`](controls.md) + [`CTRL-DECISION-001`](controls.md), retrieved-rule verification and deterministic evaluation | Medium | Risk Engineering |
| RISK-010 | Evaluation self-bias | Evaluation | Medium | Medium | [`CTRL-EVAL-001`](controls.md), hidden cases and deterministic decision scoring; LLM judge restricted to qualitative metrics | Low | AI Engineering |
| RISK-011 | Loss or ambiguity of observability evidence | Governance | Medium | High | [`CTRL-EVIDENCE-001`](controls.md) + [`CTRL-TRACE-001`](controls.md), fail-closed Phoenix export and provenance metadata | Low | Platform |
