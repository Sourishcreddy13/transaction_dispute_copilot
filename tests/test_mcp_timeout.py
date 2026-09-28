"""NFR-04 coverage: MCP tool timeouts must degrade gracefully, not hang or crash.

Two layers are tested:
1. `BankingMCPClient._invoke` actually raises `MCPToolTimeout` when a tool call
   outruns `call_timeout` (the mechanism itself, in isolation, with no real
   subprocess/server involved so this stays fast and deterministic).
2. `CopilotGraph.case_data_agent` converts that timeout into a graceful
   `NEEDS_INFO` state with a `TOOL_TIMEOUT` error code instead of letting the
   exception propagate out of the graph — for both the "no transaction_id yet"
   path (`mcp_recent`) and the "transaction_id already known" path
   (`mcp_data`). Before this test existed, nothing exercised the timeout path
   at all: `grep -rn timeout tests/` returned no hits.
"""
from __future__ import annotations

import asyncio

import pytest

from src.graph import CopilotGraph
from src.mcp_client import BankingMCPClient, MCPToolTimeout


class _HangingTool:
    """Stands in for a real MCP tool whose call never returns in time."""

    async def ainvoke(self, arguments):
        await asyncio.sleep(10)  # much longer than any call_timeout used below
        return {"never": "reached"}


def test_mcp_client_raises_timeout_on_slow_tool():
    """The client layer: a tool call slower than call_timeout must raise
    MCPToolTimeout, not hang forever or raise the raw asyncio.TimeoutError."""
    client = object.__new__(BankingMCPClient)
    client.call_timeout = 0.05
    client.tools = {"slow_tool": _HangingTool()}
    client.initialized = True

    with pytest.raises(MCPToolTimeout):
        asyncio.run(client._invoke("slow_tool", {}))


class _FakeDB:
    def set_state(self, case_id, target, review_state=None):
        pass  # no-op: this test only cares about the returned node output


class _TimeoutOnRecentServices:
    """A minimal services stand-in: `mcp_recent` always times out, `mcp_data`
    is never reached in the "no transaction_id yet" path."""

    def __init__(self):
        self.db = _FakeDB()

    async def mcp_recent(self, state):
        raise MCPToolTimeout("MCP tool 'get_recent_transactions' timed out after 0.05s")

    async def mcp_data(self, state):
        raise AssertionError("mcp_data should not be called when transaction_id is unknown")


class _TimeoutOnDataServices:
    """A minimal services stand-in: transaction_id is already known, so
    `case_data_agent` goes straight to `mcp_data`, which times out."""

    def __init__(self):
        self.db = _FakeDB()

    async def mcp_recent(self, state):
        raise AssertionError("mcp_recent should not be called when transaction_id is already known")

    async def mcp_data(self, state):
        raise MCPToolTimeout("MCP tool 'get_transaction' timed out after 0.05s")


def test_case_data_agent_degrades_gracefully_on_recent_lookup_timeout():
    """No transaction_id yet -> case_data_agent calls mcp_recent -> times out
    -> the node must return NEEDS_INFO with a TOOL_TIMEOUT error, not raise."""
    g = object.__new__(CopilotGraph)
    g.s = _TimeoutOnRecentServices()
    state = {
        "case_id": "CASE-TIMEOUT-1",
        "transaction_id": None,
        "classification": None,
        "masked_text": "",
    }

    result = asyncio.run(g.case_data_agent(state))

    assert result["case_state"] == "NEEDS_INFO"
    assert result["route"] == "finalize"
    assert result["errors"][0] == "TOOL_TIMEOUT"


def test_case_data_agent_degrades_gracefully_on_data_fetch_timeout():
    """transaction_id already known -> case_data_agent calls mcp_data ->
    times out -> the node must return NEEDS_INFO with a TOOL_TIMEOUT error,
    not raise or crash the graph."""
    g = object.__new__(CopilotGraph)
    g.s = _TimeoutOnDataServices()
    state = {
        "case_id": "CASE-TIMEOUT-2",
        "transaction_id": "T-1007",
        "classification": None,
        "masked_text": "",
    }

    result = asyncio.run(g.case_data_agent(state))

    assert result["case_state"] == "NEEDS_INFO"
    assert result["route"] == "finalize"
    assert result["errors"][0] == "TOOL_TIMEOUT"
    assert result["transaction_candidates"] == []
