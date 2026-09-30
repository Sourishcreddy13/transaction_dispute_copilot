from __future__ import annotations

import json


def test_failure_scenarios_have_stable_citations(tmp_path, monkeypatch):
    from scripts import run_failure_scenarios as mod

    report = tmp_path / "failure-scenarios.json"
    monkeypatch.setattr(mod, "REPORT", report)
    monkeypatch.setenv("ACCESS_SECRET", "x" * 40)

    class Span:
        def __init__(self):
            self.trace_id = 1
            self.span_id = 2
        def get_span_context(self):
            class Ctx:
                trace_id = 1
                span_id = 2
            return Ctx()
        def set_attribute(self, *_args, **_kwargs):
            pass
        def record_exception(self, *_args, **_kwargs):
            pass
        def set_status(self, *_args, **_kwargs):
            pass

    class TraceManager:
        provider = None
        def span(self, *_args, **_kwargs):
            from contextlib import contextmanager
            @contextmanager
            def cm():
                yield Span()
            return cm()

    monkeypatch.setattr(mod, "configure_phoenix", lambda **_kwargs: TraceManager())

    class Gateway:
        def __init__(self, *_args, **_kwargs):
            pass
        def classify(self, *_args, **_kwargs):
            return None
        def telemetry_for(self, *_args, **_kwargs):
            return {"attempts": []}
        def close(self):
            pass
    monkeypatch.setattr(mod, "SemanticGateway", Gateway)

    class FakeServer:
        def get_transaction(self, _token, transaction_id):
            raise RuntimeError("TXN_NOT_FOUND")
    monkeypatch.setitem(__import__("sys").modules, "mcp_server", type("M", (), {"server": FakeServer()})())

    rows = mod.run()
    assert len(rows) >= 3
    assert all(row.get("phoenix_ref") for row in rows[:3])
    written = json.loads(report.read_text(encoding="utf-8"))
    assert all(row.get("phoenix_ref") for row in written[:3])
