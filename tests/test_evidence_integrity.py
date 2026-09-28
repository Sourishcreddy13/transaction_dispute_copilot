from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_trace_export_has_no_non_phoenix_fallback():
    source = (ROOT / "scripts/export_traces.py").read_text(encoding="utf-8")
    assert "logs/runtime_spans.jsonl" not in source
    assert "No non-Phoenix fallback is permitted" in source


def test_required_dashboard_png_is_not_generated_by_matplotlib():
    source = (ROOT / "scripts/build_dashboard.py").read_text(encoding="utf-8")
    assert "dashboard.png" not in source
    capture = (ROOT / "scripts/capture_phoenix_dashboard.py").read_text(encoding="utf-8")
    assert "reports" in capture
    assert "dashboard.png" in capture


def test_dashboard_data_uses_phoenix_trace_export():
    source = (ROOT / "scripts/build_dashboard.py").read_text(encoding="utf-8")
    assert 'traces" / "phoenix_spans.jsonl' in source
    assert '"source": "traces/phoenix_spans.jsonl"' in source


def test_policy_evidence_is_fail_closed():
    source = (ROOT / "app/core/engines.py").read_text(encoding="utf-8")
    assert "matched = matching_hit is not None" in source
    assert "matched=False" not in source or "matching_hit is not None" in source
