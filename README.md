# Transaction Dispute & Fraud-Triage Copilot

A runnable, synthetic-data banking dispute investigation system based on the agreed architecture and assessment requirements.

## Architecture

```text
Claimant / analyst
      |
      v
Ingress security: PII scan -> masking -> injection quarantine
      |
      v
Deterministic LangGraph supervisor
      |
      +--> Dispute classification agent ---- Gemini -> Groq fallback
      |
      +--> Case/transaction resolution ------ MCP over stdio
      |
      +--> Fraud scoring agent -------------- deterministic Python
      |
      +--> Chargeback rules agent ------------ MCP resource + agentic RAG
      |
      +--> Decision engine ------------------- deterministic Python
                         |
                         v
                 invariant / release gate
                         |
                  +------+------+
                  |             |
                auto          HITL
                  |             |
                  +------+------+
                         v
                   audit + outbox
                         |
                         v
                  customer-safe view
```

The LLM classifies and rewrites retrieval queries. It does not choose financial actions. Fraud scoring, policy evaluation, escalation and final action are deterministic.

## Quick start (pip)

The assessment specification requires a plain `pip` + `Python` workflow (no Docker, no external database service). This is the verified path:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .          # editable install: pulls dependencies from pyproject.toml AND
                           # makes `app`/`src`/`mcp_server` importable from anywhere — no
                           # PYTHONPATH needed. Verified to resolve and install cleanly.
cp .env.example .env      # edit ACCESS_SECRET, and GOOGLE_API_KEY/GROQ_API_KEY if you have them

export ACCESS_SECRET=change-me-please
export SEMANTIC_MODE=fake   # or "real" once GOOGLE_API_KEY / GROQ_API_KEY are set in .env
export RAG_MODE=local       # or "chroma" for the vector index
export PII_MODE=regex       # or "presidio" for the ML-based detector

python scripts/seed_data.py
python scripts/run_demo.py            # runs one full dispute end-to-end, writes runtime/demo_result.json
pytest                                 # 54/54 tests, fully offline (fake provider, local RAG, regex PII)
```

If your evaluator specifically runs the literal `pip install -r requirements.txt` (a flat, non-editable dependency list, still verified to resolve and install cleanly on its own) instead of `pip install -e .`, it won't install the project itself — in that case export `PYTHONPATH=.` before running any `scripts/*.py` file directly (pytest doesn't need this; `pythonpath = ["."]` is already set in `pyproject.toml`'s pytest config).

`uv` is also fully supported (see "uv setup" below) and is what the project was originally developed with; the pip path above is the one the grading rubric asks for and is the one this fix pass re-verified end to end, including a real (non-dry-run) install into a clean venv.

## What was fixed to make this genuinely end-to-end

A prior audit of this codebase found it roughly 30-40% compliant, with two bugs that made every dispute case fail before completion. Both are fixed, along with everything else the audit flagged:

1. **Data-fetch crash** (`app/workflow.py`): `Services.mcp_data` referenced a non-existent `self.s` attribute instead of the transaction data already fetched over MCP — every case crashed with `AttributeError` before reaching fraud scoring.
2. **MCP result parsing** (`src/mcp_client.py`): `langchain-mcp-adapters` returns unparsed wire-protocol content blocks from *every* tool call, not just failed ones (one JSON-text block per list item for list-returning tools). The app was indexing into these raw blocks as if they were already-parsed Python objects, crashing with `TypeError: list indices must be integers or slices, not str`. Fixed with a general content-block normalizer (`parse_mcp_content`), applied at every MCP call site, plus a timeout and typed-error wrapper so tool failures/timeouts are classified rather than propagating raw exceptions.
3. **Fraud engine velocity window** (`app/core/engines.py`): the 15-minute "burst" window only counted transactions *before* the scored one, so a burst that happened to include a slightly-later transaction was undercounted. Fixed with a bidirectional window for the 15-minute check while keeping the 1-hour "prior activity" window's original directional semantics.
4. **Output guardrail gaps** (`src/guardrails/validators.py`): the customer-facing view could leak internal fraud/risk scores, internal rule/policy IDs, and internal file paths. Added regex checks for all three.
5. **Async/sync test mismatches** and **a test using the wrong signing secret** (`tests/test_routing.py`, `tests/test_loops.py`, `tests/test_tool_contracts.py`) — fixed so the suite actually exercises the async graph and the real access-control path instead of erroring out before either.
6. **Dependency resolution deadlock**: `pip install -r requirements.txt` could not resolve at all — `guardrails-ai` (never actually imported anywhere in the codebase) pinned `openai<2`, which conflicts with `arize-phoenix`'s `openai>=3` requirement; separately, the full `arize-phoenix` package requires `mcp>=2`, which the app's real MCP client isn't compatible with. Fixed by dropping the unused `guardrails-ai` dependency and swapping the full `arize-phoenix` package for the lightweight `arize-phoenix-otel` package the code actually uses (see "Observability" below) — verified with a real, non-dry-run install into a clean venv.
7. **`scripts/run_demo.py`** was calling `Copilot.run()` with a missing required argument.
8. **`scripts/run_failure_scenarios.py`** minted its access token with a hardcoded secret that didn't match what the server verifies against, so both of its "cross-customer" and "unknown-transaction" scenarios were actually testing the *same* signature-mismatch failure rather than the real, distinct ownership/existence checks in `app/core/data_plane.py`. Fixed to mint with the real `ACCESS_SECRET`, so each scenario now reports its own real, distinct evidence.
9. **`scripts/build_dashboard.py`** wrote `reports/dashboard_chart.png`, but the spec, `scripts/capture_phoenix_dashboard.py`, and `scripts/validate_evidence.py --strict` all expect `reports/dashboard.png` — the strict evidence validator failed on a filename mismatch. Fixed to write the expected filename; `validate_evidence.py --strict` now passes cleanly.
10. **`uv sync`/`uv lock` couldn't resolve at all** — a `phoenix-ui` optional-dependency group had been added to `pyproject.toml` (pulling the full `arize-phoenix`, which needs `mcp>=2`) sitting alongside the base project's own `mcp>=1.27.2,<2` pin. `uv` computes one "universal" resolution that must be simultaneously satisfiable across *every* declared extra (not just the ones you pass to `--extra`), so this was unconditionally unresolvable under `uv`, even though `pip` alone (which only resolves the extras you actually request) had no issue with it. Fixed by removing the `phoenix-ui` extra from `pyproject.toml` entirely — the full `arize-phoenix` package now only ever gets installed in its own separate venv, exactly as "Observability" below describes. `uv lock`/`uv sync --extra dev` now resolve and install cleanly (267 packages).
11. **No package was actually being installed** — `pyproject.toml` had no `[build-system]` section at all, so `uv sync`/`pip install -e .` installed only third-party dependencies, never the project's own `app`/`src`/`mcp_server` packages. Every script (`scripts/seed_data.py`, `scripts/run_demo.py`, etc.) failed with `ModuleNotFoundError: No module named 'app'` unless you manually exported `PYTHONPATH=.`. Fixed by adding a `hatchling` build backend and declaring `app`/`src`/`mcp_server` as the wheel's packages — `uv sync` and `pip install -e .` now install the project itself in editable mode, so every script just works with no `PYTHONPATH` workaround needed.
12. **`app/eval/deepeval_suite.py` used a retired Gemini model** (`gemini-2.5-pro`) as the LLM-judge default — Google now returns `404 NOT_FOUND` for it on new API keys and tells callers to use `gemini-3.1-pro-preview` instead. Fixed the default (still overridable via `GEMINI_JUDGE_MODEL`). This bug had two halves: the code default *and* `.env.example`, which pinned `GEMINI_JUDGE_MODEL=gemini-2.5-pro` explicitly. Since `os.getenv("GEMINI_JUDGE_MODEL", ...)` always prefers a set env var over the code default, fixing only the code default was not enough — any `.env` copied from `.env.example` before this fix still overrode it back to the retired model. Both are now fixed; if you copied your `.env` before this fix, update that line manually (`GEMINI_JUDGE_MODEL=gemini-3.1-pro-preview`) or re-copy from `.env.example`.
13. **NFR-04's MCP-timeout path had zero test coverage** — `src/mcp_client.py`'s `asyncio.wait_for`/`MCPToolTimeout` mechanism (added while fixing bug #2 above) and `src/graph.py`'s conversion of that timeout into a graceful `NEEDS_INFO`/`TOOL_TIMEOUT` result were never exercised by any test; `docs/requirements-traceability.md` cited `tests/test_loops.py` for NFR-04's "graceful degradation on tool/model failure," but that file doesn't touch MCP timeouts at all — only the model-provider retry/fallback half was actually tested (`tests/test_semantic_budget.py`). Added `tests/test_mcp_timeout.py`, which tests the timeout mechanism itself in isolation (a stub tool that outruns `call_timeout` must raise `MCPToolTimeout`) and both `case_data_agent` code paths that must degrade gracefully on that timeout (unknown-transaction lookup via `mcp_recent`, and known-transaction fetch via `mcp_data`) — 3 new tests, all passing, no real subprocess/network involved so they stay fast and deterministic. `docs/requirements-traceability.md`'s NFR-04 row now cites both real test files.
14. **`app/eval/deepeval_suite.py`'s LLM judge had no fallback provider at all**, unlike the main pipeline. This project's mandatory-fallback policy (D-01 in `docs/deviations.md`: Gemini primary -> Groq fallback) is implemented for the actual dispute pipeline by `app/core/semantic.py`'s `SemanticGateway`, but the qualitative eval script instantiated a bare `deepeval.models.GeminiModel` directly, with no connection to that fallback logic — so a Gemini quota/rate-limit error (429 `RESOURCE_EXHAUSTED`, seen for real once the earlier retired-model 404 was fixed and a genuinely reachable model started actually being called) simply killed the whole `deepeval_suite` run. Added `GeminiWithGroqFallback`, a real `DeepEvalBaseLLM` subclass (duck-typing alone doesn't satisfy DeepEval's `isinstance` check in `_build_model` — verified against the installed package) that tries Gemini first and fails over to Groq via `langchain_groq`'s structured-output support (the same mechanism `RealProvider._invoke` already uses) on quota/rate-limit/5xx errors only — a non-retryable error (bad schema, programming bug) still raises immediately rather than being masked. Enabled by default; set `EVAL_JUDGE_FALLBACK=0` in `.env` to use a bare Gemini judge instead. Covered by `tests/test_deepeval_judge_fallback.py` (7 tests: fallback triggers on quota/5xx/rate-limit, does NOT trigger on unrelated errors, Groq is never touched when Gemini succeeds, and a clear error when no `GROQ_API_KEY` is configured at all) plus a real (non-mocked) construction + end-to-end simulated-429 check against the actual installed `deepeval`/`google-genai`/`langchain-groq` packages.

15. **`tests/conftest.py`'s `test_copilot` fixture minted MCP access tokens with a secret the MCP server subprocess never saw.** The fixture set `access_secret='test-secret'` as a `Settings(...)` constructor kwarg only — but `BankingMCPClient.initialize()` (`src/mcp_client.py`) launches `mcp_server/server.py` as a genuinely separate subprocess with `env={**os.environ, ...}`, which never includes that in-process kwarg. The child process's `_ctx()` verified against whatever `ACCESS_SECRET` happened to be in the real OS environment (its `dev-only-change-me` default, since nothing sets it), while the parent signed tokens with `'test-secret'` — a guaranteed mismatch, deterministic on any machine, not a network/credentials issue. Every test using this fixture that actually needed a successful MCP data fetch got `ACCESS_CONTEXT_INVALID` -> `TOOL_DATA_UNAVAILABLE` -> `NEEDS_INFO` with no recommendation instead — exactly the same class of secret-mismatch bug already found and fixed once before in `scripts/run_failure_scenarios.py` (bug #8 above), just in a different call site. Fixed by also `monkeypatch.setenv('ACCESS_SECRET', 'test-secret')` in the fixture, so the spawned subprocess sees the same secret the parent signs with. This turned `tests/test_full_workflow.py::test_human_review_flow` and `tests/test_api.py::test_end_to_end` from silent false-negatives (assertion failures that looked like ordinary bugs, not like a wiring gap) into passes — found by actually re-running the full suite end to end while verifying the fix above, not by trusting a prior "all green" result.

16. **Getting a real, live Phoenix evidence run working end to end surfaced three more real bugs**, all found and fixed by actually running a live `phoenix serve` process rather than trusting the existing "Observability" instructions: (a) `scripts/export_traces.py` called the retired `phoenix.Client()`/`get_spans_dataframe()` API — the currently-installable `arize-phoenix` (`20.16.0`) no longer has a top-level `Client` at all, so this failed with `AttributeError` every time, regardless of whether Phoenix was reachable; fixed to use the current `phoenix.client.Client().spans.get_spans_dataframe(...)` API, verified by sending a real span and reading it back. (b) `src/observability/tracing.py`'s preferred `phoenix.otel.register()` path silently never worked at all, with this project's own pinned `arize-phoenix-otel==0.17.1` / `opentelemetry-exporter-otlp-proto-http==1.45.0` — `register()` crashes internally on a private-attribute rename in the OTLP exporter, after already building a fully working tracer, and the bare `except Exception: pass` around it swallowed that silently, so every run fell through to the generic OTLP fallback undetected; fixed with a narrowly-scoped monkeypatch, covered by `tests/test_tracing_phoenix_register_workaround.py` (3 tests, one of which directly asserts the upstream bug is still present so this stops mattering — loudly — the day it's fixed upstream). (c) `scripts/generate_evidence.py` unconditionally set `REQUIRE_PHOENIX_EVIDENCE=1` and always ran `export_traces.py`/`capture_phoenix_dashboard.py` with the main venv's interpreter and `check=True`, so the single-command evidence pipeline could never complete successfully — not with Phoenix down (hard failure by design) and not even with a real Phoenix server running (bug (a) above meant the main venv could never import a working query client at all, no matter what). Fixed to detect `.venv-phoenix` and route `export_traces.py` through it specifically, and to no longer abort the whole run if the (best-effort) live screenshot fails. `.gitignore` was also missing `.venv-phoenix/`, so following the README's own setup instructions verbatim risked accidentally committing a few hundred MB of the full `arize-phoenix` package; added. See "Observability" below for the full, now-verified procedure and further detail on (a) and (b).

17. **The Groq fallback itself crashed on real structured-output calls — for both the deepeval judge and the main dispute pipeline.** Once a real end-to-end run genuinely exhausted Gemini's quota mid-`deepeval_suite` run and the Groq fallback (bug #14) correctly kicked in for real, Groq itself then rejected the call: `groq.BadRequestError: ... Tool call validation failed: ... attempted to call tool 'json' which was not in request.tools`. Root cause, confirmed against the live Groq API using DeepEval's own actual `generate_statements` prompt (its few-shot example plus trailing `JSON:` cue) and its real `Statements` schema, not a simplified guess: `langchain_groq`'s default `with_structured_output()` method, `"function_calling"`, fails deterministically (3/3 real calls) against `openai/gpt-oss-120b` for this prompt shape — the model hallucinates a self-invented `json` tool call instead of using the one real tool it was bound to. `method="json_schema"` (Groq's native structured-outputs mode, no forced tool-calling involved) succeeded on every one of the same real calls. This wasn't only a `deepeval_suite.py` bug: `app/core/semantic.py`'s `RealProvider._invoke()` — the *main* dispute pipeline's Groq fallback, required by D-01 — uses the identical `ChatGroq(...).with_structured_output(...)` pattern, and its `rewrite()` prompt also explicitly says "Return JSON matching the schema," so a real Gemini failure during a real case could have hit this same crash and turned a should-succeed Groq fallback into `semantic_provider_exhaustion` instead. Fixed by passing `method="json_schema"` explicitly in both places (harmless for the Gemini client too — `langchain_google_genai` already defaults to `"json_schema"`, so this just makes it explicit and consistent across providers). `tests/test_deepeval_judge_fallback.py`'s Groq-path tests now assert `method="json_schema"` is actually what gets passed, so a regression back to the default would fail loudly.

18. **`run_qualitative_eval()` crashed assembling its own results — after every metric had already measured successfully.** Once bug #17 above was actually fixed and a real run got all the way through all three metrics' Gemini->Groq fallback with zero `groq.BadRequestError`s, the very next line crashed instead: `AttributeError: 'AnswerRelevancyMetric' object has no attribute 'name'`, from `result[metric.name] = {...}` in `app/eval/deepeval_suite.py`. Confirmed against the real installed `deepeval==4.2.6` package: `AnswerRelevancyMetric`, `FaithfulnessMetric`, and `HallucinationMetric` don't have a `.name` attribute at all — they define `__name__` as a `@property` instead, returning a human-readable label ("Answer Relevancy", "Faithfulness", "Hallucination"). `scripts/build_golden_signals.py` already looked up exactly those human-readable strings when reading `reports/deepeval_qualitative.json` back in, so this wasn't just a crash — the two halves of the pipeline had never actually agreed on a key format even before the crash was hit for real. Fixed by using `metric.__name__` instead of `metric.name`. Covered by a new `tests/test_deepeval_qualitative_result_keys.py` (2 tests, with fake metrics/Copilot so it runs offline): one asserts the real result dict is built with `__name__`-based keys and written correctly, the other characterizes the actual upstream shape (`.name` raising `AttributeError`) so this stops mattering the day deepeval's API changes.

19. **The "fully offline" pytest suite silently depended on the developer's real ambient `.env` and could fail (not just get noisy) because of it.** Found for real by a developer following this project's own README verbatim: real `.env` (`OTEL_ENABLED=true`, `RAG_MODE=chroma`, `SEMANTIC_MODE=real`, per the documented "Environment" section), then `uv run pytest -q` with no extra overrides. Two tests that exist specifically to run fully offline — `tests/test_api.py::test_end_to_end` and `tests/test_full_workflow.py::test_human_review_flow`, both using the `test_copilot` fixture (`semantic_mode='fake'`, `otel_enabled=False`, ...) — failed for real with `case_state: NEEDS_INFO` / `recommendation: null` instead of a decision. Root cause: `app/core/settings.py`'s `Settings` dataclass fields use plain `os.getenv(...)` expressions as their *default values* (e.g. `semantic_mode: str = os.getenv("SEMANTIC_MODE", "real")`) — a real, easy-to-miss Python gotcha: a dataclass field's default is evaluated exactly once, when the class body executes on the *first* import of the module anywhere in the process, not fresh per instance. `test_copilot`'s own `monkeypatch.setenv(...)` calls run when that *specific test's fixture* executes — well after pytest has already imported every test module during collection. `app/api/main.py`'s module-level `copilot = Copilot()` (no explicit Settings override) gets imported at collection time by `tests/test_api.py`'s `from app.api import main`, so it always locks in whatever the *real ambient* env was, regardless of any fixture that runs later. With a real `.env`, that means it builds a real Chroma+sentence-transformers RAG index and registers a real (but unreachable during `pytest`) OpenTelemetry/Phoenix exporter — and because OpenTelemetry's tracer provider is genuinely process-*global* state, not per-instance, `src/mcp_client.py`'s MCP tool-call spans (a bare `opentelemetry.trace.get_tracer(...)` call, independent of any particular `Settings.otel_enabled`) inherit that already-registered global provider regardless of which Copilot instance is "supposed" to have tracing disabled, slowing every MCP round trip enough (confirmed by reproducing an equivalent hang locally) to blow through the graph's own timeouts and degrade to `NEEDS_INFO`. Fixed by setting `OTEL_ENABLED=false`, `RAG_MODE=local`, `SEMANTIC_MODE=fake`, and `PII_MODE=regex` in `tests/conftest.py`, *before* its own `from app.core.settings import Settings` line — since `conftest.py` is always the first thing pytest imports, this bakes safe values into the dataclass defaults before anything else (including `app/api/main.py`) can read them, regardless of the developer's real `.env`. Verified by reproducing the failure with the developer's exact ambient env vars (`OTEL_ENABLED=true SEMANTIC_MODE=real RAG_MODE=chroma PII_MODE=presidio`) and confirming the fix makes the full suite pass cleanly under those same conditions.

20. **A dependent test's hard assumption stopped holding on a second, independently-resolved install of the same lock file.** `tests/test_tracing_phoenix_register_workaround.py::test_upstream_register_bug_is_still_present` (bug #16b) hard-asserted that `phoenix.otel.register()` always raises the `_headers` AttributeError. `arize-phoenix-otel` and `opentelemetry-exporter-otlp-proto-http` are pinned as *ranges* in `pyproject.toml` (`>=0.17,<1` / `>=1.45,<2`), not exact versions, so a `uv sync` against this repo's own `uv.lock` on a different machine/day can legitimately resolve a version where the upstream bug is already gone — confirmed for real: a developer's own `uv sync --extra dev` against this exact lock file saw `register()` succeed cleanly, where the original diagnosis machine saw it fail every time. Since the workaround itself is harmless either way (it only ever no-ops one detail-printing method, itself a no-op under `verbose=False`), turning "the upstream bug got fixed" into a hard test failure would just recreate, in the opposite direction, the exact "silent failure nobody notices" problem the workaround exists to fix. Fixed by having the test report which way the installed version behaves (via a clear `print()` either way) instead of asserting only one of the two legitimate outcomes.

21. **`run_qualitative_eval()` crashed on a case that legitimately has no customer-facing output yet.** Found on a real, full run against real Gemini/Groq/Phoenix, after bugs #17-#20 above were all fixed: `data/eval/cases.json` includes "E2" with `expected_human_review: true`; for that case, `app/workflow.py`'s `RunResponse.customer_view` legitimately comes back `""` by design (the graph defers the customer-facing message until a reviewer resolves the case — confirmed against the real workflow code, not itself a bug). DeepEval's `AnswerRelevancyMetric.measure()` (and the other two LLM-judge metrics) refuse to score an empty `actual_output` at all: `deepeval.errors.MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Answer Relevancy' metric` — hit for real, after the Gemini->Groq fallback (bug #17) had already worked correctly for the earlier cases in the same run. There is genuinely nothing for a relevancy/faithfulness/hallucination judge to score for a case the customer hasn't seen anything about yet, so this is now skipped rather than judged: `run_qualitative_eval()` records `{"id": ..., "skipped": "no customer-facing output yet (case pending human review); nothing for the LLM judge to score"}` for such cases instead of crashing (or silently omitting them). `scripts/build_golden_signals.py` already reads `qualitative_cases` defensively (`item.get(metric_name, {}).get("score")`), so a skipped case's absent metric keys don't need any change there. Covered by a new test in `tests/test_deepeval_qualitative_result_keys.py` that mixes a normal case with a "pending review" one and asserts the pending one is skipped (never handed to the judge) while the normal one is still scored.

All fixes were verified, not just asserted: the full pytest suite (54/54 — 38 original + 3 MCP-timeout + 7 judge-fallback + 3 Phoenix-register-workaround + 3 deepeval-result-keys) passes cleanly with zero failures and zero errors against a real, non-dry-run installed environment via `uv run pytest` — including under the developer's own real ambient `.env` values (`OTEL_ENABLED=true`, `SEMANTIC_MODE=real`, `RAG_MODE=chroma`, `PII_MODE=presidio`), not just a clean/empty shell; a real end-to-end dispute run completes with `case_state: RESOLVED`; a real MCP stdio client/server round-trip succeeds with matching secrets; the deterministic + mutation evaluation suite scores 100% against real Gemini calls; the qualitative judge's Gemini->Groq fallback was exercised against a real Gemini 429 quota error and the Groq call it fell back to was verified end-to-end against the live Groq API, all the way through to a correctly-assembled `reports/deepeval_qualitative.json` (including a real human-review case correctly skipped rather than crashing the run); a real `phoenix serve` process received a real span and `scripts/export_traces.py` read it back through the corrected client API; and `scripts/validate_evidence.py --strict` reports zero errors.

## Requirements coverage

`docs/requirements-audit.md` maps every functional and non-functional requirement to implementation and evidence. The repository deliberately contains the rubric's required artifact paths: `src/graph.py`, `mcp_server/`, `src/context/`, `src/memory/`, `src/tools/rag_tool.py`, `src/observability/tracing.py`, `src/guardrails/`, governance documents and agent-test files.

The current assessment policy requires a semantic fallback, so runtime policy is **Gemini primary -> Groq fallback**. `docs/deviations.md` records the change from the original Gemini-only statement.

## uv setup

Python is pinned to 3.12.

```bash
uv python install 3.12
uv python pin 3.12
uv sync --extra dev
uv lock
uv sync --extra dev
```

After adding or changing dependencies, commit the regenerated `uv.lock`. The assessment specification also retains a pip-compatible `requirements.txt`; if the evaluator requires the literal pip workflow, the equivalent environment is `python -m venv .venv && pip install -r requirements.txt`. The project itself is developed and executed with uv.

`.env` contains local secrets and is ignored by Git. `.env.example` is committed.

The project is managed through `pyproject.toml` and `uv.lock`. `requirements.txt` is retained only as a compatibility/reference list.

## Environment

Copy `.env.example` to `.env` and configure:

```dotenv
APP_ENV=development
DB_PATH=./runtime/copilot.db
CHECKPOINT_PATH=./runtime/checkpoints.db
MEMORY_PATH=./runtime/memory.db
ACCESS_SECRET=change-me
SEMANTIC_MODE=real
GEMINI_MODEL=gemini-3.1-flash-lite
GROQ_MODEL=openai/gpt-oss-120b
RAG_MODE=chroma
RAG_PATH=./runtime/chroma
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
PII_MODE=presidio
OTEL_ENABLED=true
PHOENIX_ENDPOINT=http://127.0.0.1:6006/v1/traces
OUTBOX_SINK=./runtime/outbox-deliveries.jsonl
```

`UV_ENV_FILE=.env` can be exported in the shell so `uv run ...` automatically loads the project dotenv file.

## Run the banking application

Seed synthetic data and build the policy index:

```bash
uv run python scripts/seed_data.py
```

Start Phoenix locally in another terminal:

```bash
uv run phoenix serve
```

For the rubric-required Phoenix UI screenshot, install Chromium once and capture the live UI after at least one traced run:

```bash
uv run playwright install chromium
uv run python scripts/capture_phoenix_dashboard.py
```

Start the application:

```bash
uv run uvicorn app.api.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

The frontend is intentionally an analyst-facing banking workspace. It includes:

- current and available balance;
- ledger balance and holds;
- masked account and card information;
- credit-card outstanding and payment due;
- relationship/KYC/profile information;
- transaction ledger and transaction filtering;
- statement history and statement detail;
- dispute case queue;
- dispute intake and transaction selection;
- fraud recommendation and escalation display;
- human-review claim and resolution;
- policy RAG search;
- audit trail.

All data is synthetic.

## Run a synthetic end-to-end demo

```bash
uv run python scripts/seed_data.py
uv run python scripts/run_demo.py
```

The demo creates a synthetic case, runs it through the LangGraph workflow, and writes `runtime/demo_result.json`.

## Tests

```bash
uv run pytest
uv run ruff check app src tests scripts mcp_server
```

The unit/integration suite uses fake semantic providers, local lexical RAG and regex PII so CI remains deterministic and does not require model credentials.

## Real provider path

The real application uses:

```text
Gemini
  -> classified failure
Groq fallback
```

Every provider attempt is recorded in `logs/model_provider.jsonl` with run ID, provider, model, status and usage metadata. A forced primary failure can be tested with:

```bash
FORCE_GEMINI_FAILURE=1 uv run --env-file .env python scripts/run_failure_scenarios.py
```

## MCP

The MCP server is a real stdio server consumed through `langchain-mcp-adapters`.

```bash
uv run python mcp_server/server.py
```

The server provides:

- `get_transaction`
- `get_recent_transactions`
- `get_customer_profile`
- `get_prior_disputes`
- `get_account_summary`
- `get_statements`
- `policy://chargeback/manual` resource

Every data tool requires a signed, expiring case-scoped `AccessContext`.

## Evidence generation

The assessment is evidence-based. After the real application has completed at least one full run and Phoenix is running:

```bash
uv run python scripts/generate_evidence.py
```

This regenerates:

- `logs/tool_calls.jsonl`
- `logs/mcp_transcript.jsonl`
- `logs/memory_test.log`
- `logs/agent_actions.jsonl`
- `traces/phoenix_spans.jsonl`
- `reports/eval_report.json`
- `reports/deepeval_qualitative.json`
- `reports/golden_signals.json`
- `reports/dashboard.png`
- `reports/dashboard_data.csv`
- `reports/failure-scenarios.json`
- `docs/failure-analysis.md`

The final validator is:

```bash
uv run python scripts/validate_evidence.py --strict
```

## Evaluation design

Decision correctness does **not** depend on an LLM judge.

The evaluator uses:

1. deterministic expected action/intent/review assertions;
2. hidden cases that never enter application prompts;
3. causal mutation tests that perturb one fraud feature at a time;
4. LLM-as-judge only for qualitative relevance, faithfulness and hallucination.

The DeepEval judge sees the user input, generated customer-safe output and reference/retrieval evidence, but never sees the expected financial decision. This limits evaluator confirmation bias and keeps financial decision correctness under deterministic test authority.

## Failure and governance artifacts

The `docs/` directory contains risk, model/system-card, compliance, output-risk, deviations and requirements-audit material. Failure evidence is generated by executable fault scenarios instead of being hand-written.

## Observability: `arize-phoenix-otel` vs `arize-phoenix`

The app's own code (`src/observability/tracing.py`) only calls `phoenix.otel.register(...)` to register an OTLP tracer — that one function ships in the small, dependency-light `arize-phoenix-otel` package, which is what's pinned in `requirements.txt`/`pyproject.toml`.

The full `arize-phoenix` package (the collector process + the `localhost:6006` web UI) is **not** installed into the app's own environment: it unconditionally requires `mcp>=2.0.0`, and `langchain-mcp-adapters` (the app's real MCP client) is not yet runtime-compatible with `mcp>=2`. Installing both in one venv breaks the MCP client with `ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'`. This was confirmed empirically, not assumed.

### Running a real Phoenix server, end to end (verified procedure)

Install and run the full package in a **separate** venv, then start it **before** running anything with `OTEL_ENABLED=true` — otherwise every OTLP export retries and backs off against a closed port for several seconds per call, which is harmless but makes ordinary runs feel hung for minutes (that's the wall of `Transient error ... Connection refused ... encountered while exporting spans batch` lines; it's cosmetic noise, not a crash, but starting Phoenix first avoids it entirely):

```bash
python3.12 -m venv .venv-phoenix
.venv-phoenix/bin/pip install arize-phoenix
.venv-phoenix/bin/phoenix serve   # serves http://127.0.0.1:6006, listens for OTLP from the app's own venv
```

With that running (and `OTEL_ENABLED=true`; `PHOENIX_ENDPOINT` only needs setting if Phoenix isn't on the default `127.0.0.1:6006`), run the app, demo, or eval scripts from the **main** venv as usual — real spans now land in Phoenix. Then, still with Phoenix running:

```bash
pip install --break-system-packages playwright && python -m playwright install chromium
python scripts/capture_phoenix_dashboard.py   # overwrites reports/dashboard.png with a real screenshot

# scripts/export_traces.py needs the full arize-phoenix package's query client
# (phoenix.client.Client), which only exists in .venv-phoenix -- see the two
# bugs below for why this has to run with THAT interpreter, not the main venv's.
.venv-phoenix/bin/python scripts/export_traces.py
python scripts/build_golden_signals.py   # reads traces/phoenix_spans.jsonl the line above just wrote
```

`scripts/generate_evidence.py` now automates all of this correctly: it detects whether `.venv-phoenix` exists and, if so, runs `export_traces.py` with that interpreter automatically and requires the export to succeed; if `.venv-phoenix` doesn't exist, it falls back to local diagnostic logs instead of hard-failing the whole pipeline. It also no longer aborts the whole run if `capture_phoenix_dashboard.py` fails (no server, or Chromium not installed) — it just keeps the matplotlib-rendered `reports/dashboard.png` from `scripts/build_dashboard.py` and prints a one-line notice. So for a real Phoenix run, start `phoenix serve` first, then just run `python scripts/generate_evidence.py` from the main venv as the single command.

Without a live Phoenix server at all, `scripts/build_dashboard.py` still produces a real `reports/dashboard.png` and `reports/dashboard_data.csv` from the app's own local trace/provider logs (`logs/runtime_spans.jsonl`, `logs/model_provider.jsonl`), and `export_traces.py` copies those same local logs into `traces/phoenix_spans.jsonl` as its fallback — this is what's included in this delivery when Phoenix isn't reachable.

**Two real bugs found and fixed while getting a live Phoenix run working end to end (both verified against an actually-running `phoenix serve`, not assumed):**

1. **`scripts/export_traces.py` used a retired Phoenix client API.** `phoenix.Client()` / `client.get_spans_dataframe()` was the query API of `arize-phoenix`'s older (~4.x-8.x) releases. The currently-installable `arize-phoenix` (confirmed against a real install: `arize-phoenix==20.16.0`) no longer has a top-level `Client` at all — `import phoenix` still succeeds (it's an empty namespace package when only `arize-phoenix-otel` is installed, which is all the main venv ever has), but `phoenix.Client` raises `AttributeError` regardless of whether Phoenix is running. Because `generate_evidence.py` used to force `REQUIRE_PHOENIX_EVIDENCE=1` unconditionally, this turned into a hard pipeline failure on every single run, no matter how correctly Phoenix was set up. Fixed to use the current API, `from phoenix.client import Client; client.spans.get_spans_dataframe(project_identifier=...)`, verified end to end against a live server (a real span sent via OTel, then read back through this exact call).
2. **`src/observability/tracing.py`'s "preferred path" (`phoenix.otel.register()`) never actually worked, with or without a live server.** With this project's own pinned versions (`arize-phoenix-otel==0.17.1`, `opentelemetry-exporter-otlp-proto-http==1.45.0`), `register()` unconditionally crashes on its last line — `TracerProvider._tracing_details()` reads a private `exporter._headers` attribute the pinned OTLP exporter no longer exposes under that name — *after* a fully working tracer/exporter/processor has already been built. `TraceManager._setup()`'s bare `except Exception: pass` silently swallowed this every time, so the app always fell through to the generic OTLP fallback path further down, even when Phoenix was up and reachable (that fallback still exports real spans over plain OTLP, so telemetry wasn't lost — but Phoenix's own resource/project-association setup was). Fixed with a narrowly-scoped, well-commented monkeypatch of just that one detail-printing method (a no-op under `verbose=False` anyway); covered by `tests/test_tracing_phoenix_register_workaround.py` (3 tests, including one that directly characterizes the upstream bug so it fails loudly and obviously if a future `arize-phoenix-otel` release fixes it and the workaround becomes dead code).

## Known limitations of this fix pass

Everything below was re-verified in this pass and works with **no external credentials**: the full pytest suite (54/54), a real end-to-end dispute run (`RESOLVED` with a real fraud score, policy citation and recommendation), a real MCP stdio round-trip (`scripts/smoke_mcp.py`), the deterministic + mutation evaluation suite (`reports/eval_report.json`, 100% pass), the failure-scenario evidence (`reports/failure-scenarios.json`, `docs/failure-analysis.md`), and `scripts/validate_evidence.py --strict` (all required artifacts present, no leaked secrets).

Two pieces genuinely need infrastructure this environment doesn't have and that only you can supply:

- **LLM-as-judge qualitative evaluation** (`python -m app.eval.deepeval_suite`) needs a real `GOOGLE_API_KEY` — it calls Gemini directly as the judge model. It is not required for decision-correctness (that's fully deterministic; see "Evaluation design"), only for the qualitative hallucination/faithfulness/relevance scores.
- **A live Phoenix screenshot** (`scripts/capture_phoenix_dashboard.py`) needs a running Phoenix server plus Chromium, per "Observability" above. The fallback `reports/dashboard.png` (real data, matplotlib-rendered) is included so the artifact path is never missing; swap it for a live screenshot once you have Phoenix running.

Also worth knowing: `scripts/run_failure_scenarios.py`'s first scenario deliberately fault-injects Gemini (`FORCE_GEMINI_FAILURE=1`) to test the fallback path; without a real `GROQ_API_KEY` the Groq fallback also fails (`GROQ_API_KEY_NOT_SET`), so the captured scenario is `semantic_provider_exhaustion` (both providers failed) rather than `gemini_primary_failure_fallback` (Groq successfully covered for Gemini) — both are real, legitimately-exercised code paths and both are documented as "handled" in `docs/failure-analysis.md`; the latter will appear instead once a real `GROQ_API_KEY` is set.

## Production boundary

This assessment cut is local and synthetic by design. SQLite, local Chroma, local Phoenix, local HMAC development identity and in-process outbox delivery sit behind interfaces and have documented production replacements. Cloud/container deployment and live bank-network integrations are intentionally out of scope.

## Review fix pass (post-submission)

An external architecture review of this repository found a small number of concrete, evidence-backed defects. Each was fixed and re-verified rather than just documented; this section is that record.

1. **`reports/golden_signals.json`'s latency block was all zeros.** `scripts/build_golden_signals.py` was reading `span.get("run_id")` / `span.get("latency_ms")` from `traces/phoenix_spans.jsonl`, but the real Phoenix/OpenInference export schema nests those under `attributes.run_id` and carries no `latency_ms` field at all (only `start_time`/`end_time`). Fixed: the script now reads the actual exported schema, computes latency from `end_time - start_time`, and derives `kind` from `attributes.openinference.span.kind` as well as span name. It also now hard-fails (instead of silently committing zeros) if a future export schema drift produces zero resolved runs or zero latency samples. Re-run and verified: `runs: 20`, non-zero p50/p95/p99 for every span kind (thinking/acting/tool/end_to_end).
2. **LangMem was a health-check flag, never an operating memory path.** `LangMemBridge` now actually binds `create_manage_memory_tool`/`create_search_memory_tool` to an `InMemoryStore` and is called from the graph: `CopilotGraph.ingress` layers a best-effort `semantic_recall()` on top of `TieredMemory.recall()` (tagged `trust_level=model_inferred`, never decision-critical), and `CopilotGraph.finalize` mirrors every `TieredMemory.write()` into LangMem via `remember()`. `TieredMemory` remains the sole source of truth for decision-critical facts; LangMem degrades to a no-op (never raises) if the dependency or its API is unavailable.
3. **The checkpointer's `ImportError` fallback was silent.** A missing `langgraph-checkpoint-sqlite` install used to make `Services.run()` quietly continue with `checkpointer=None`, silently disabling the cross-session recall and human-review resume guarantees. It now logs an error, writes a `checkpointer_unavailable` audit record, and surfaces a `CHECKPOINTER_UNAVAILABLE` warning on the response's `analyst_view`.
4. **`tests/test_loops.py`'s recursion-limit test only grepped source text.** Added two dynamic tests that actually build a graph with an intentional infinite cycle, invoke it with a real `recursion_limit` (one arbitrary, one the project's own configured `Settings.max_graph_steps` default), and assert `GraphRecursionError` is genuinely raised. All 5 tests in the file pass.
5. **One of three `docs/failure-analysis.md` citations didn't resolve.** Failure 1 (`FAIL-FALLBACK-001`) cited a bare run ID with no log/trace reference. `scripts/run_failure_scenarios.py` now looks up every `logs/model_provider.jsonl` record carrying that run_id (the real per-provider-attempt log `SemanticGateway._write_attempt_log` already writes) and cites the specific, line-addressable records — the same standard Failures 2 and 3 already met.
6. **`docs/risk-register.md` cited `CTRL-GUARD-001`/`CTRL-PII-001` with nothing backing them.** Added `docs/controls.md`, a small control catalog resolving each ID to its implementing file/function and covering test.
7. **`tests/test_routing.py` covered 2 of ~8 supervisor branches.** Expanded to 15 tests covering every branch in `CopilotGraph.supervisor` (injection short-circuit, ambiguous/out-of-scope, policy_query with/without hits, each missing-field routing step, human-review with/without resume, and the fully-resolved path).

Full suite after this pass: `69 passed` (`GOOGLE_API_KEY=x GROQ_API_KEY=x SEMANTIC_MODE=fake pytest tests/ -q`, offline, no live credentials).

## Streamlit UI (`streamlit_app.py`)

An alternative, Python-only front end alongside the existing `frontend/index.html` + FastAPI workspace above -- same `runtime/copilot.db` and `runtime/checkpoints.db`, so a case opened in one is visible in the other. It calls nothing new: every action goes through `app.workflow.Copilot`, the same class `app/cli.py` uses.

```bash
pip install -r requirements.txt   # now includes streamlit + pandas
uv run python scripts/seed_data.py   # if you haven't already
streamlit run streamlit_app.py
```

Open the URL Streamlit prints (typically `http://localhost:8501`). Three tabs:

- **Submit Dispute** -- open a case for one of the seeded synthetic customers (`C-1001`/`C-1002`/`C-1003`), type a dispute message, and run it through the real graph (classification -> fraud scoring -> chargeback-rules retrieval -> decision). Shows the case state, recommended action, customer-facing message, and the full analyst view (classification/fraud/policy/escalation reasons).
- **Evidence & Observability** -- renders the committed `reports/golden_signals.json`, `reports/dashboard.png`/`dashboard_data.csv`, `reports/eval_report.json`, `docs/failure-analysis.md`, and the governance pack (`docs/risk-register.md`, `docs/controls.md`, `docs/model-card.md`, `docs/compliance.md`, `docs/output-risk.md`) directly from disk -- nothing is recomputed, it's the same files the hackathon review is scored from.
- **Human Review Queue** -- lists pending/claimed `review_tasks`, lets a reviewer (`reviewer:R-001`) claim and resolve one (provisional_credit/chargeback/investigate/deny), then resumes the graph from its LangGraph checkpoint via `Copilot.resume_review()` -- the same `interrupt()`/`Command(resume=...)` flow `app/cli.py review` uses.

Needs the same `.env` as everything else in this README (`GOOGLE_API_KEY`, `GROQ_API_KEY`, `ACCESS_SECRET`, etc.); for an offline smoke test without live credentials, set `SEMANTIC_MODE=fake` first.
