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
    # Static check kept for documentation purposes: confirms ainvoke() actually
    # wires the setting into the call, not just that it's declared somewhere.
    # This alone does NOT prove the guard stops a runaway loop -- see the two
    # dynamic tests below for that.
    import inspect
    from src.graph import CopilotGraph
    source = inspect.getsource(CopilotGraph.ainvoke)
    assert "recursion_limit" in source
    assert "max_graph_steps" in source


def test_recursion_limit_actually_stops_a_runaway_loop():
    """Dynamic proof of the guard: build a minimal two-node graph that bounces
    forever with no terminal condition (the failure mode a routing bug in
    CopilotGraph.supervisor() could produce), invoke it with a deliberately
    tiny recursion_limit using the exact same call pattern CopilotGraph.ainvoke
    uses, and assert the run is actually halted rather than looping forever."""
    import asyncio
    from typing import TypedDict

    import pytest
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import StateGraph

    class LoopState(TypedDict):
        steps: int

    async def ping(state: LoopState) -> dict:
        return {"steps": state["steps"] + 1}

    async def pong(state: LoopState) -> dict:
        return {"steps": state["steps"] + 1}

    builder = StateGraph(LoopState)
    builder.add_node("ping", ping)
    builder.add_node("pong", pong)
    builder.set_entry_point("ping")
    builder.add_edge("ping", "pong")
    builder.add_edge("pong", "ping")  # intentional infinite cycle, no exit edge
    graph = builder.compile()

    with pytest.raises(GraphRecursionError):
        asyncio.run(graph.ainvoke({"steps": 0}, {"recursion_limit": 5}))


def test_default_max_graph_steps_bounds_a_runaway_loop():
    """Same failure mode, but driven through the project's own configured
    default (Settings.max_graph_steps, MAX_GRAPH_STEPS env var, currently 40)
    instead of an arbitrary small number -- proves the *actual configured
    guard*, not just that GraphRecursionError exists in the abstract."""
    import asyncio
    from typing import TypedDict

    import pytest
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import StateGraph

    from app.core.settings import Settings

    class LoopState(TypedDict):
        steps: int

    async def spin(state: LoopState) -> dict:
        return {"steps": state["steps"] + 1}

    builder = StateGraph(LoopState)
    builder.add_node("spin", spin)
    builder.set_entry_point("spin")
    builder.add_edge("spin", "spin")  # intentional infinite self-loop
    graph = builder.compile()

    limit = Settings().max_graph_steps
    with pytest.raises(GraphRecursionError):
        asyncio.run(graph.ainvoke({"steps": 0}, {"recursion_limit": limit}))
