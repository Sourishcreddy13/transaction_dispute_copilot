import asyncio

from src.graph import CopilotGraph

def test_supervisor_routes_classification_first():
    class S: pass
    g = object.__new__(CopilotGraph)
    result = asyncio.run(g.supervisor({"case_state":"CLASSIFIED","classification":None}))
    assert result["route"] == "classification_agent"

def test_supervisor_routes_needs_info_to_finalize():
    g = object.__new__(CopilotGraph)
    result = asyncio.run(g.supervisor({"case_state":"NEEDS_INFO"}))
    assert result["route"] == "finalize"
