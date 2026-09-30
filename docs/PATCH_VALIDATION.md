# Patch Validation

This file records checks executed against the corrected source tree.

## Passed

```text
python -m compileall -q app src mcp_server scripts tests streamlit_app.py
python scripts/validate_evidence.py --strict
bash -n scripts/local_run.sh scripts/run_smoke.sh
```

Additional direct checks cover:

- operation-ledger idempotency scoping and stale-worker rejection
- signed access-context verification and nonce replay rejection
- bearer authentication and role enforcement
- PII masking and prompt-injection quarantine
- MCP unknown-response rejection
- bounded local RAG retrieval
- Phoenix golden-signals/dashboard consistency

## Full pytest status

The full pytest suite requires the pinned Python 3.12 environment and the declared LangGraph dependency set. The supplied execution environment uses Python 3.13 and does not have `langgraph` installed; package installation is unavailable because outbound package resolution is disabled. Therefore full pytest collection is not claimable from this environment.

The repository remains configured for the specified Python 3.12 target and the committed regression tests are included in the ZIP.
