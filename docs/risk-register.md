# Risk Register

| ID | Risk | Category | Likelihood | Impact | Mitigation | Residual | Owner |
|---|---|---|---|---|---|---|---|
| RISK-001 | Prompt injection in claimant text | OWASP LLM01 | High | High | `CTRL-GUARD-001`, trust-zone isolation, no tools for LLM | Medium | AI Engineering |
| RISK-002 | Sensitive data disclosure | OWASP LLM02 | Medium | High | `CTRL-PII-001`, sink allow-lists, customer-safe renderer | Low | Security |
| RISK-003 | Unauthorized customer access | OWASP / IAM | Medium | Critical | signed `AccessContext`, case ownership checks | Low | Platform |
| RISK-004 | RAG corpus poisoning | OWASP LLM08/LLM03 | Medium | High | manifest hash, corpus lint, instruction/data separation | Low | AI Engineering |
| RISK-005 | Provider failure | Reliability | Medium | High | bounded retry, breaker, Gemini→Groq fallback | Low | Platform |
| RISK-006 | LLM overreach into financial decisions | Agency | Medium | Critical | deterministic fraud/policy/decision engines | Low | AI Engineering |
| RISK-007 | Duplicate side effects | Reliability | Medium | High | idempotency ledger + outbox | Low | Platform |
| RISK-008 | Reviewer race / stale decision | Workflow | Medium | High | versioned compare-and-set + claim lease | Low | Platform |
| RISK-009 | Incorrect policy recommendation | Domain correctness | Medium | Critical | YAML single source, invariants, deterministic evaluation | Medium | Risk Engineering |
| RISK-010 | Evaluation self-bias | Evaluation | Medium | Medium | hidden cases, deterministic scoring, Gemini judge restricted to qualitative metrics | Low | AI Engineering |
