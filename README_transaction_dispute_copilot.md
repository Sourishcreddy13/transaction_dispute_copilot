# Transaction Dispute & Fraud-Triage Copilot

A local synthetic-banking copilot built around LangGraph, MCP, agentic RAG, tiered memory, Phoenix observability, deterministic fraud/policy controls, human-in-the-loop review, security guardrails, and agent-level evaluation.

This repository is designed for the **Agentic AI Engineer Pathway — Cross-Cutting Capstone Hackathon**. The implementation keeps the decision-critical controls deterministic: the LLM performs semantic interpretation, while transaction eligibility, fraud scoring, policy matching, escalation, and release invariants are enforced by application logic.

---

## 1. What the system does

A synthetic card-dispute enters through the secure intake layer and is processed as follows:

```text
Customer dispute
        │
        ▼
Secure intake
PII masking + prompt-injection detection + quarantine
        │
        ▼
LangGraph supervisor
        │
        ├── Dispute classification ─────── Gemini → Groq fallback
        │
        ├── Case/evidence retrieval ─────── MCP tools
        │
        ├── Fraud scoring ───────────────── deterministic engine
        │
        └── Chargeback policy retrieval ─── MCP resource + Agentic RAG
        │
        ▼
Deterministic decision engine
        │
        ├── Automatic outcome
        │
        └── Human-review interrupt
                │
                ▼
          SQLite checkpoint
                │
                ▼
          Reviewer claim/resolve
                │
                ▼
          Disposition + finalization
        │
        ▼
Audit trail + outbox + customer-safe response
        │
        ▼
Phoenix / OpenTelemetry evidence
```

### Technology stack

| Area | Implementation |
|---|---|
| Language | Python 3.12–3.13 |
| Agent framework | LangGraph |
| Primary LLM | Google Gemini |
| Fallback LLM | Groq |
| Interoperability | MCP Python SDK + `langchain-mcp-adapters` |
| Checkpointing | SQLite + `langgraph-checkpoint-sqlite` |
| Memory | SQLite authoritative memory + LangMem semantic overlay |
| Retrieval | Chroma / local lexical fallback + Sentence-Transformers |
| Observability | Arize Phoenix + OpenTelemetry / OpenInference |
| Evaluation | deterministic golden-set checks + DeepEval qualitative judge |
| API | FastAPI |
| Browser UI | static HTML/JS served by FastAPI |
| Optional UI | Streamlit |
| Security | custom policy validators, PII masking, signed access context, bearer-token API auth |

The data plane is fully synthetic. No live banking, card-network, fraud, sanctions, or customer-system integration is required for this cut.

---

## 2. Project structure

```text
transaction_dispute_copilot_fixed/
│
├── app/
│   ├── api/                 FastAPI application and protected endpoints
│   ├── core/                DB, security, RAG, PII, telemetry, settings, outbox
│   ├── eval/                deterministic + DeepEval evaluation harnesses
│   └── workflow.py          high-level copilot orchestration
│
├── src/
│   ├── graph.py             LangGraph state, supervisor, workers, routing
│   ├── mcp_client.py        MCP client + response-contract validation
│   ├── context/             context engineering and quarantine
│   ├── memory/              durable + semantic memory
│   ├── observability/       Phoenix/OpenTelemetry instrumentation
│   ├── guardrails/          input/output security validators
│   └── execution/           execution journey / run metadata
│
├── mcp_server/
│   └── server.py            custom MCP server: tools + resource
│
├── frontend/
│   └── index.html           banking operations console
│
├── data/
│   ├── synthetic/           customers, accounts, cards, transactions, statements, disputes
│   ├── policy_corpus/       chargeback/dispute policy documents used by RAG
│   ├── policy/              policy reference copies
│   └── eval/                public, hidden and mutation evaluation data
│
├── tests/                   unit, integration, security and agent-specific regression tests
├── scripts/                 seed, smoke, evidence, dashboard and trace tooling
├── reports/                 generated evaluation/dashboard evidence
├── traces/                  generated Phoenix span export
├── logs/                    generated audit/tool/provider evidence
├── runtime/                 local SQLite/checkpoint/vector/cache files
├── config/                  provider and decision configuration
└── docs/                    architecture, controls, governance, evidence and deviations
```

---

## 3. Requirements

Use either Python 3.12 or 3.13. The current project environment in the tested workflow uses Python 3.12.

Verify the environment:

```bash
python --version
python -c "from importlib.metadata import version; print('LangChain:', version('langchain')); print('LangGraph:', version('langgraph'))"
```

The second command uses package metadata for LangGraph because the package does not expose `langgraph.__version__` reliably across versions.

### Install a new environment

Using an existing environment is also supported; you do not need to rebuild it when the dependencies already work.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

With `uv`:

```bash
uv sync --extra dev
```

---

## 4. Configure `.env`

Copy the template:

```bash
cp .env.example .env
```

At minimum, configure:

```text
GOOGLE_API_KEY=<Gemini key>
GROQ_API_KEY=<Groq fallback key>
ACCESS_SECRET=<32+ random characters>
API_TOKENS_JSON=<synthetic analyst/reviewer token mapping>
```

For the real pipeline, the important runtime settings are:

```text
SEMANTIC_MODE=real
RAG_MODE=chroma
PII_MODE=presidio
OTEL_ENABLED=true
PHOENIX_ENDPOINT=http://127.0.0.1:6006/v1/traces
```

The template also defines limits for provider calls, graph steps, context size, MCP timeouts, outbox retries, high-value escalation, and evaluation models.

### Never commit `.env`

`.env.example` is the committed template. `.env` contains local credentials and must stay untracked.

---

## 5. First-time seed

Populate the local synthetic banking data and index the policy corpus:

```bash
python scripts/seed_data.py
```

Expected output includes:

```text
Indexed policy documents: 4
Demo case: CASE-XXXXXXXXXX
Seed complete
```

The seed command can be rerun to rebuild the local synthetic environment.

---

# 6. Run the application

There are three useful modes.

## A. Full browser application

The browser UI is served by FastAPI. You do **not** need a separate React/Vite/npm development server.

### Terminal 1 — Phoenix

Only needed when tracing is enabled:

```bash
source .venv-phoenix/bin/activate
phoenix serve
```

Leave this terminal running.

### Terminal 2 — FastAPI application

```bash
source .venv/bin/activate
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

The root endpoint serves `frontend/index.html`.

### Browser authentication

The UI asks for a bearer token. Use the token mapped to the synthetic analyst in `API_TOKENS_JSON`.

The browser stores the token in `sessionStorage` for the current browser session and sends it as:

```http
Authorization: Bearer <token>
```

There is no anonymous access to customer, account, transaction, case, review, audit, or RAG endpoints.

### Frontend workflow

```text
Sign in
  ↓
Select customer
  ↓
Review account / card / statements / transactions
  ↓
Select transaction
  ↓
Submit dispute
  ↓
Watch agent execution journey
  ↓
Inspect fraud and policy evidence
  ↓
Review recommendation
  ↓
Handle human-review task when required
  ↓
Inspect audit / final case state
```

The frontend is intentionally an operations console rather than a consumer banking product. Visual polish is not a graded requirement for this project.

---

## B. CLI investigation

Open a case with an explicit customer and transaction:

```bash
python -m app.cli open-case \
  --customer C-1002 \
  --transaction T-2001
```

Copy the returned case ID, for example:

```text
CASE-25A98A4567
```

Run the dispute:

```bash
python -m app.cli run \
  --case CASE-25A98A4567 \
  --input samples/dispute.json
```

A high-risk/high-value transaction such as `T-2001` exercises the full investigation path and normally reaches `PENDING_REVIEW`.

### Synthetic test transaction: `T-2001`

```text
Customer:        C-1002
Amount:          INR 85,000
Merchant:        Unknown Electronics
Country:         US
Card present:    false
Authentication:  none
Device:          new-device
Channel:         ecommerce
IP country:      US
```

This transaction is useful for exercising fraud scoring, policy retrieval, escalation, checkpointing, and human review.

---

## C. Deterministic smoke run

The deterministic smoke path avoids live provider calls and is intended for fast local verification:

```bash
./scripts/local_run.sh
```

The script sets safe local defaults such as:

```text
SEMANTIC_MODE=fake
RAG_MODE=local
PII_MODE=regex
OTEL_ENABLED=false
```

It then runs the deterministic evaluation, memory persistence test, MCP smoke test, pytest, and non-strict evidence validation.

This mode is a software verification path. It must not be presented as the submission-grade Phoenix evidence run.

---

# 7. Human-in-the-loop review

A high-risk/high-value case can pause at the LangGraph human-review node and persist its state in the SQLite checkpointer.

The investigation result includes:

```text
case_state: PENDING_REVIEW
review_task.task_id: TASK-XXXXXXXXXX
review_task.version: 0
```

Resolve the task with the **actual** task ID and version returned by the application:

```bash
python -m app.cli review \
  --task TASK-XXXXXXXXXX \
  --version 0 \
  --action investigate \
  --reason-code HIGH_RISK \
  --note "Manual review completed for high-risk unauthorized transaction."
```

Do not type placeholder values such as `<TASK_ID>` literally in `zsh`.

The CLI atomically claims the pending review, refreshes the task version, resolves it, and resumes the checkpointed graph.

A successful review cycle ends approximately as:

```text
PENDING_REVIEW
      ↓
CLAIMED
      ↓
RESOLVED
      ↓
disposition_commit
      ↓
finalize
      ↓
case_state = RESOLVED
```

---

# 8. Phoenix observability

The application and the full Phoenix collector/UI are intentionally installed in separate environments. The main application uses Phoenix/OpenTelemetry-compatible instrumentation; the full Phoenix server runs separately on port `6006`.

### Create the Phoenix environment

```bash
python3.12 -m venv .venv-phoenix
.venv-phoenix/bin/pip install arize-phoenix
```

If the project already has a working `.venv-phoenix`, keep using it.

### Start Phoenix

```bash
.venv-phoenix/bin/phoenix serve
```

Phoenix UI:

```text
http://127.0.0.1:6006
```

The application exporter targets:

```text
http://127.0.0.1:6006/v1/traces
```

If you see `Connection refused` to port `6006`, Phoenix is not running or is listening on a different endpoint.

---

# 9. Submission-grade evidence generation

Run this only after Phoenix is running and real provider credentials are configured.

The evidence pipeline:

1. seeds synthetic data;
2. verifies cross-session memory;
3. runs MCP smoke coverage;
4. runs deterministic evaluation;
5. runs DeepEval qualitative evaluation;
6. runs failure scenarios;
7. exports Phoenix spans;
8. generates golden signals;
9. generates dashboard data and summary;
10. captures the Phoenix dashboard screenshot;
11. normalizes developer-local filesystem paths;
12. runs strict evidence validation.

Run:

```bash
python scripts/generate_evidence.py
```

A successful run ends with:

```text
python scripts/validate_evidence.py --strict
{
  "ok": true,
  "strict": true,
  "errors": []
}

Evidence generation completed.
```

### Generated evidence

```text
traces/phoenix_spans.jsonl
traces/phoenix_spans.jsonl.meta.json

reports/golden_signals.json
reports/dashboard_data.csv
reports/dashboard_data.csv.meta.json
reports/dashboard.png
reports/dashboard.png.capture-meta.json
reports/dashboard_summary.png
reports/eval_report.json
reports/deepeval_qualitative.json
reports/failure-scenarios.json
reports/evidence_run_meta.json

logs/tool_calls.jsonl
logs/agent_actions.jsonl
logs/model_provider.jsonl
logs/runtime_spans.jsonl
logs/mcp_transcript.jsonl
logs/mcp_resource_smoke.json
logs/memory_test.log
```

### Evidence rules

`reports/dashboard.png` must come from the Phoenix UI capture path. The locally generated `reports/dashboard_summary.png` is supplemental and is not a replacement for the Phoenix screenshot.

Policy and evidence references are normalized to repository-relative paths before strict validation.

---

# 10. Evaluation

## Pytest

Run the complete regression suite:

```bash
python -m pytest -q
```

The suite covers API security, IDOR, routing, loop/cascade protection, MCP contracts, RAG behavior, memory, outbox semantics, semantic deadlines, authentication, and frontend security.

## Deterministic evaluation

```bash
python -m app.eval.run_eval
```

The golden set includes public and hidden cases plus causal mutation cases. Decision correctness is evaluated with deterministic reference assertions rather than allowing the judge model to select the expected business action.

## DeepEval qualitative evaluation

```bash
python -m app.eval.deepeval_suite
```

DeepEval is used for qualitative hallucination, faithfulness, and answer-relevance assessment.

The evaluation code records Gemini as the preferred judge provider and can use the configured Groq fallback when Gemini returns a retryable provider/quota failure. Such deviations are documented in `docs/deviations.md`.

---

# 11. Useful Make targets

```bash
make install          # install/sync the project
make seed             # seed synthetic data
make run              # start FastAPI with reload
make mcp              # run the MCP server directly
make test             # run pytest
make lint             # Ruff checks
make format-check     # Ruff formatting check
make typecheck        # mypy
make eval             # deterministic evaluation
make deep-eval        # DeepEval qualitative evaluation
make demo             # run demo script
make phoenix          # start Phoenix
make traces           # export Phoenix traces
make evidence         # full evidence pipeline
make smoke            # deterministic smoke run
make check            # lint + pytest
```

---

# 12. API surface

The FastAPI application exposes protected operations for:

```text
GET  /
GET  /health
GET  /api/health/details

GET  /api/customers
GET  /api/customers/{customer_id}/dashboard
GET  /api/customers/{customer_id}/transactions
GET  /api/customers/{customer_id}/statements
GET  /api/customers/{customer_id}/statements/{statement_id}
GET  /api/customers/{customer_id}/account
GET  /api/customers/{customer_id}/card

POST /api/cases
GET  /api/cases
GET  /api/cases/{case_id}
POST /api/cases/{case_id}/run
GET  /api/cases/{case_id}/audit

GET  /api/analyst/queue
GET  /api/reviews/{task_id}
POST /api/reviews/{task_id}/claim
POST /api/reviews/{task_id}/resolve

POST /api/outbox/drain
GET  /api/rag/search
```

Sensitive endpoints require a valid bearer token and server-side customer/case authorization.

FastAPI streaming remains optional for this cut; the current browser application uses the standard API endpoints.

---

# 13. Security model

The implementation treats customer-provided text as untrusted data.

Controls include:

- bearer-token API authentication;
- server-side principal and customer/case authorization;
- reviewer ownership checks;
- signed access context for MCP calls;
- PII masking before downstream processing;
- prompt-injection detection and quarantine;
- output sanitization and browser-side escaping;
- MCP input/output contract validation;
- bounded provider calls, context, graph steps and tool calls;
- audit logging for consequential actions;
- idempotent operation handling;
- outbox retry/claim controls;
- no real secrets in committed files.

Synthetic PANs/account numbers are masked wherever shown, and logs are designed to avoid plaintext sensitive identifiers.

Advanced OAuth, live secret rotation, containerized deployment, and real banking integrations are outside the scope of this hackathon cut.

---

# 14. Runtime and evidence troubleshooting

## `CASE_NOT_FOUND`

Use the case ID returned by:

```bash
python -m app.cli open-case --customer ... --transaction ...
```

Do not replace it with an example ID or placeholder.

## `REVIEW_CONFLICT`

Check the task's current version before resolving it. For a new pending task the normal sequence is version `0` → claim → version `1` → resolve.

## Phoenix `Connection refused` on `127.0.0.1:6006`

Start Phoenix in the separate Phoenix environment:

```bash
.venv-phoenix/bin/phoenix serve
```

Then retry the application.

## Hugging Face unauthenticated warning

The Sentence-Transformers model can still load without `HF_TOKEN`; the message is a Hub authentication/rate-limit warning, not an application exception. A token can be configured when higher Hub rate limits are useful.

## Gemini `429 RESOURCE_EXHAUSTED`

The provider is rejecting the request because of quota/rate limits. The semantic/evaluation code records the failure and can use the configured Groq fallback where the fallback policy applies.

## LangGraph checkpoint deserialization warning

Warnings about unregistered `app.models.*` checkpoint types are emitted by the serialization layer when trusted application classes are restored. The current application path continues to operate, but the checkpoint serializer configuration should be kept aligned with the installed LangGraph version before upgrading the dependency.

---

# 15. Evidence and governance documents

Key documentation lives under `docs/`:

```text
architecture.md
controls.md
deviations.md
evaluation-design.md
evidence-status.md
failure-analysis.md
model-card.md
output-risk.md
requirements-audit.md
requirements-traceability.md
risk-register.md
PATCH_VALIDATION.md
```

These documents describe the architecture, controls, limitations, evidence lineage, risk/compliance mapping, and evaluation methodology.

---

# 16. Recommended local workflow

For normal development:

```bash
# Terminal A
cd ~/Downloads/transaction_dispute_copilot_fixed
source .venv/bin/activate
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

```bash
# Terminal B — only when Phoenix is needed
cd ~/Downloads/transaction_dispute_copilot_fixed
source .venv-phoenix/bin/activate
phoenix serve
```

Then use:

```text
http://127.0.0.1:8000/
```

For a CLI test:

```bash
python -m app.cli open-case --customer C-1002 --transaction T-2001
python -m app.cli run --case <REAL_CASE_ID> --input samples/dispute.json
```

For regression:

```bash
python -m pytest -q
```

For submission evidence:

```bash
python scripts/generate_evidence.py
```

For final evidence verification:

```bash
python scripts/validate_evidence.py --strict
```

A final submission should be generated from the committed repository state, not from an uncommitted working tree.

---

# 17. Scope and limitations

This is a local, synthetic implementation intended to demonstrate the full engineering surface of an agentic dispute-triage system:

```text
Agentic core
+ context engineering
+ MCP
+ retrieval
+ memory
+ observability
+ cost/latency governance
+ guardrails
+ auditability
+ governance/compliance evidence
+ agent evaluation
+ regression testing
```

It is not a real banking transaction processor and does not connect to production card networks, customer information systems, fraud vendors, sanctions systems, or live chargeback networks.

The goal of the repository is to make the complete decision path executable, observable, controlled, testable, and reproducible from synthetic data.
