from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_repository_artifacts_exist():
    required = [
        "src/graph.py",
        "mcp_server/server.py",
        "src/context/engineering.py",
        "src/memory/store.py",
        "src/tools/rag_tool.py",
        "src/observability/tracing.py",
        "src/guardrails/validators.py",
        "tests/test_routing.py",
        "tests/test_loops.py",
        "tests/test_tool_contracts.py",
        "tests/test_memory_persistence.py",
        "docs/risk-register.md",
        "docs/model-card.md",
        "docs/compliance.md",
        "docs/output-risk.md",
    ]
    missing = [p for p in required if not (ROOT / p).exists()]
    assert not missing, missing


def test_required_evidence_generators_exist():
    for rel in [
        "scripts/generate_evidence.py",
        "scripts/export_traces.py",
        "scripts/build_golden_signals.py",
        "scripts/build_dashboard.py",
        "scripts/capture_phoenix_dashboard.py",
        "scripts/run_failure_scenarios.py",
        "app/eval/deepeval_suite.py",
    ]:
        assert (ROOT / rel).exists(), rel
