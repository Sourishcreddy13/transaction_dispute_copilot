import os

# Real, verified bug (found running the full suite in a real developer
# environment with a real `.env`, not hypothesized): `app/core/settings.py`'s
# `Settings` dataclass fields use plain `os.getenv(...)` calls as their
# *default values* -- e.g. `semantic_mode: str = os.getenv("SEMANTIC_MODE",
# "real")`. A dataclass field's default expression is evaluated exactly
# once, when the class body executes (i.e. on the very first import of
# `app.core.settings` anywhere in the process) -- NOT fresh per instance. So
# `test_copilot`'s own `monkeypatch.setenv(...)` below, which only runs once
# a specific test's fixture executes, can never retroactively change what
# any module-level singleton built with NO explicit override already baked
# in. `app/api/main.py` has exactly such a singleton: `copilot = Copilot()`
# at module level (line 15), built with zero explicit Settings overrides,
# so it always gets whatever `OTEL_ENABLED`/`RAG_MODE`/`SEMANTIC_MODE`
# happened to be in the real ambient environment (a real developer's
# `.env`: `OTEL_ENABLED=true`, `RAG_MODE=chroma`, `SEMANTIC_MODE=real`) at
# COLLECTION time, when `tests/test_api.py`'s `from app.api import main`
# first imports it -- well before this fixture ever runs.
#
# Confirmed for real against a real developer environment following this
# project's own README setup instructions verbatim (`cp .env.example .env`,
# real API keys, `uv run pytest -q`, no extra env overrides): both
# `tests/test_api.py::test_end_to_end` and
# `tests/test_full_workflow.py::test_human_review_flow` -- despite both
# using the supposedly-fully-offline `test_copilot` fixture below -- failed
# with `case_state=NEEDS_INFO`/`recommendation=None` instead of a real
# decision. Root cause: `app/api/main.py`'s module-level `copilot =
# Copilot()`, built with the real ambient settings, registers a real
# OpenTelemetry/Phoenix exporter pointed at an unreachable Phoenix server.
# OpenTelemetry's tracer provider is process-*global* state, not
# per-instance, so `src/mcp_client.py`'s MCP tool-call spans (a bare
# `opentelemetry.trace.get_tracer(...)` call, independent of any particular
# `TraceManager`/`Settings.otel_enabled`) pick up that already-registered
# global provider regardless of which Copilot instance is "supposed" to
# have tracing disabled, real chroma/embedding-model initialization included
# -- slowing (and, in the real run that surfaced this, timing out) MCP round
# trips for tests that never asked for any of that.
#
# Fixed by setting these here, BEFORE the `Settings` import below (which is
# also the very first import of `app.core.settings` in the whole pytest
# process, since pytest always loads `conftest.py` before collecting any
# test module) -- so the dataclass field defaults bake in safe, offline
# values regardless of the developer's real `.env`, restoring the "fully
# offline" guarantee the README already claims for `pytest` instead of it
# silently depending on whatever the ambient environment happens to be.
os.environ["OTEL_ENABLED"] = "false"
os.environ["RAG_MODE"] = "local"
os.environ["SEMANTIC_MODE"] = "fake"
os.environ["PII_MODE"] = "regex"

import pytest
from app.core.settings import Settings
from app.core.db import DB
from app.workflow import Copilot
@pytest.fixture
def test_copilot(tmp_path,monkeypatch):
    # ACCESS_SECRET must ALSO be a real env var, not just a Settings(...) kwarg:
    # BankingMCPClient.initialize() (src/mcp_client.py) spawns mcp_server/server.py
    # as a genuinely separate subprocess with env={**os.environ, ...} — it never
    # sees this fixture's in-process `Settings.access_secret` override at all.
    # Without this line, the parent process mints access-context tokens signed
    # with 'test-secret' while the child verifies them against whatever
    # ACCESS_SECRET happens to be in the ambient environment (the ACCESS_SECRET
    # default 'dev-only-change-me' if unset), so every MCP call fails with
    # ACCESS_CONTEXT_INVALID -> TOOL_DATA_UNAVAILABLE -> NEEDS_INFO with no
    # recommendation, exactly the same class of secret-mismatch bug already
    # found and fixed in scripts/run_failure_scenarios.py.
    test_secret='test-secret-0123456789-abcdef-0123456789'
    monkeypatch.setenv('SEMANTIC_MODE','fake'); monkeypatch.setenv('RAG_MODE','local'); monkeypatch.setenv('PII_MODE','regex'); monkeypatch.setenv('OTEL_ENABLED','false'); monkeypatch.setenv('ACCESS_SECRET',test_secret)
    s=Settings(db_path=str(tmp_path/'test.db'),semantic_mode='fake',rag_mode='local',pii_mode='regex',otel_enabled=False,access_secret=test_secret)
    return Copilot(db=DB(s.db_path),settings=s)
