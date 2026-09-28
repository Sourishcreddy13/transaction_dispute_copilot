# Control Catalog

Referenced by `docs/risk-register.md`. Per the Citation-Resolves Rule, every
control ID cited as a mitigation must resolve to a committed, executable
artifact -- this file is that resolution layer: each ID below points at the
exact file/function that implements it, plus the test that exercises it.

| Control ID | Implements | Code | Test |
| --- | --- | --- | --- |
| `CTRL-GUARD-001` | Prompt-injection detection + quarantine of untrusted customer text before it enters graph state | `src/context/engineering.py::ContextEngineer.isolate` (the `INJECTION` regex sets `injection_flag`/`quarantined=True` on every inbound customer message) + `src/graph.py::CopilotGraph.supervisor` (short-circuits to `finalize` with a fixed `SECURITY_FLAG` refusal the moment `injection_flag` is set, so a detected injection attempt never reaches a tool-calling or decision node) | `tests/test_security.py`, `tests/test_context_engineering.py` |
| `CTRL-PII-001` | PII/PAN masking before persistence, plus output-side leak prevention | `app/core/pii.py::PIIService.scan` (masks card/account numbers in `masked_text` before it is written to `logs/`, `traces/`, or the SQLite context/checkpoint stores -- see the security-boundary comment in `app/workflow.py`) + `src/guardrails/validators.py::validate_customer_view` (`SCORE_LEAK_RE`/`RULE_ID_RE`/`INTERNAL_PATH_RE` block internal fraud scores, policy rule IDs, and file paths from ever reaching the customer-facing view) | `tests/test_pii.py`, `tests/test_guardrails.py` |

Adding a new `CTRL-*` ID to `docs/risk-register.md` requires adding a row here
in the same change, so no risk-register citation is ever left dangling.
