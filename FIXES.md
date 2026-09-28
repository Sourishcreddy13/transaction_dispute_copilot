# Correctness / Production Review Fixes

This file records the corrections applied to the supplied repository.

## Critical

1. **Phoenix provenance** — non-Phoenix runtime spans are no longer copied to `traces/phoenix_spans.jsonl`.
2. **Phoenix dashboard provenance** — `reports/dashboard.png` is only written by the Phoenix UI screenshot script. Matplotlib now writes `reports/dashboard_summary.png`.
3. **Phoenix source lineage** — `reports/dashboard_data.csv` is built from `traces/phoenix_spans.jsonl`, not local runtime diagnostics.
4. **RAG evidence correctness** — policy rules are not marked matched unless their source is present in retrieved RAG hits.
5. **RAG execution path** — the graph consumes `AgenticPolicyRAG`, including bounded rewrite behavior, instead of bypassing the required agentic tool wrapper.
6. **Fraud velocity** — 15-minute velocity is now an actual score feature.
7. **Fraud time window** — 30-day behavioral statistics are calculated from a real 30-day window.
8. **Provider provenance concurrency** — provenance is stored per `run_id`, avoiding cross-request contamination from mutable `last_*` fields.
9. **Human-review resume** — reviewer resolution now resumes the checkpointed graph before returning the final state.

## Reliability

10. MCP resource calls have the same explicit timeout boundary as MCP tools.
11. The checkpoint backend is required for the main workflow instead of silently degrading.
12. Project-relative data/config paths resolve from the repository root.
13. `Settings` uses `default_factory`, so environment variables are read when a `Settings()` object is created rather than once at module import.
14. MCP client/server and data-plane path handling is independent of the caller's current working directory.

## Testing

15. Added RAG citation regression coverage.
16. Added 15-minute velocity regression coverage.
17. Added untrusted-text quarantine coverage.
18. Expanded MCP contract coverage across all tools.
19. Human-review test exercises graph resume and reviewer attribution.

## Evidence rule

The corrected implementation deliberately prefers a hard failure over an inaccurate artifact. That means a Phoenix-required evidence command can fail until Phoenix is actually available; it does not silently substitute local telemetry.
