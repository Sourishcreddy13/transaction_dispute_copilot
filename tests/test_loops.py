def test_needs_info_is_terminal_for_current_turn():
    import asyncio
    from src.graph import CopilotGraph
    g=object.__new__(CopilotGraph)
    result=asyncio.run(g.supervisor({"case_state":"NEEDS_INFO"}))
    assert result["route"]=="finalize"

def test_graph_has_bounded_terminal_finalize():
    from src.graph import CopilotGraph
    assert hasattr(CopilotGraph, "finalize")


def test_graph_invocation_declares_recursion_limit():
    import inspect
    from src.graph import CopilotGraph
    source = inspect.getsource(CopilotGraph.ainvoke)
    assert "recursion_limit" in source
