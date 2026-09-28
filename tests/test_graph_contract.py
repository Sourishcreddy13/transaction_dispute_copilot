from pathlib import Path


def test_authoritative_langgraph_contains_required_workers_and_finalize():
    text = (Path(__file__).resolve().parents[1] / "src/graph.py").read_text()
    for node in [
        '"classification_agent"',
        '"fraud_scoring_agent"',
        '"chargeback_rules_agent"',
        '"finalize"',
    ]:
        assert node in text


def test_requirements_use_uv_with_compatible_requirements_reference():
    root = Path(__file__).resolve().parents[1]
    assert (root / "requirements.txt").exists()
    assert "uv sync" in (root / "README.md").read_text()

def test_graph_has_all_required_rubric_artifacts():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in [
        "src/context/engineering.py", "src/memory/store.py", "src/tools/rag_tool.py",
        "src/observability/tracing.py", "src/guardrails/validators.py",
        "mcp_server/server.py", "docs/failure-analysis.md", "README.md",
    ]:
        assert (root / rel).exists(), rel
