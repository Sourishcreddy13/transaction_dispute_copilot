# Deviations and Runtime Decisions

## D-01 — Gemini primary with Groq fallback

The supplied specification contains both a Gemini-only stack statement and an explicit requirement for Groq as fallback. This implementation uses Gemini as the primary semantic provider and Groq only for retry/fallback paths. Provider attempts are recorded in machine-generated provenance.

## D-02 — Local synthetic banking data plane

Real card-network, fraud, sanctions, bureau and customer-system integrations are out of scope. The data plane is synthetic and local.

## D-03 — Phoenix collector/query client in a separate environment

The application emits OpenTelemetry/Phoenix-compatible spans from the main environment. The full Phoenix collector/UI/query client is installed in `.venv-phoenix` so its dependency tree does not conflict with the MCP client stack. The evidence exporter therefore runs with `.venv-phoenix/bin/python` and queries the live Phoenix project.

Local runtime span logs are diagnostics only. They are never copied into `traces/phoenix_spans.jsonl` and never satisfy the Phoenix evidence requirement.

## D-04 — Semantic memory overlay

SQLite is the authoritative persistent memory layer. LangMem provides the semantic recall overlay. Because the in-process LangMem store is not itself durable, the overlay is rehydrated from verified SQLite facts when a new application process/session accesses a customer. This preserves cross-session persistence while keeping unverified semantic matches out of the decision-critical path.

## D-05 — Phoenix telemetry scopes

Evidence generation starts a fresh evidence window and exports Phoenix spans from
that window only. Application latency KPIs are computed from traces containing a
`copilot.run` root. Token/cost KPIs are computed from all LLM spans in that
window because the current LangChain instrumentation exports some LLM calls as
separate traces; this scope is stated explicitly in `reports/golden_signals.json`
and `reports/dashboard_data.csv.meta.json`.

## D-06 — Provider pricing basis

Cost evidence uses provider-specific standard text-token pricing configured in
`config/providers.yaml`: Gemini 3.1 Flash-Lite at $0.25/1M input and $1.50/1M
output tokens, and Groq GPT-OSS 120B at $0.15/1M input and $0.60/1M output
based on the published rates checked for the 2026-09-28 evidence run.
