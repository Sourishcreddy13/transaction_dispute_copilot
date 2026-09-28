from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


class MCPClientError(RuntimeError):
    pass


class MCPToolTimeout(MCPClientError):
    pass


class MCPToolError(MCPClientError):
    """Raised when the MCP server reported a tool-execution error.

    langchain-mcp-adapters surfaces a failed tool call as a *successful*
    ``ainvoke`` whose result is the MCP error-content shape (a list of
    ``{"type": "text", "text": "Error executing tool ...: <message>"}``
    blocks) rather than as a raised exception. Without this check, that
    list is handed straight to business logic that expects a dict (e.g.
    ``account["account"]``), which fails far from the real cause with an
    unrelated ``TypeError``.
    """


def _block_text(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("text")
    return getattr(block, "text", None)


def _looks_like_mcp_error_content(result: Any) -> str | None:
    """Return the error message if `result` is an MCP tool-error payload, else None."""
    if isinstance(result, list) and result:
        text = _block_text(result[0])
        if isinstance(text, str) and ("error executing tool" in text.lower() or text.lower().startswith("error")):
            return text
    return None


def parse_mcp_content(result: Any, expect: str | None = None) -> Any:
    """Normalize langchain-mcp-adapters' raw tool result into a plain Python value.

    A FastMCP tool that returns a plain (non-structured-content) Python value
    comes back over stdio as a *list of TextContent blocks*, not as the
    parsed value itself: a tool returning a single ``dict`` comes back as one
    block whose ``text`` is that dict's JSON; a tool returning ``list[dict]``
    comes back as **one block per list item** (not one block holding a JSON
    array). Block count alone can't always tell a bare dict from a one-item
    list, so callers that know their tool returns a list pass
    ``expect="list"`` to disambiguate a single-item result; ``expect="dict"``
    is accepted for symmetry/clarity at call sites.
    """
    if not isinstance(result, list):
        return result
    if not result:
        return [] if expect == "list" else result

    texts = [_block_text(b) for b in result]
    if any(t is None for t in texts):
        return result  # not a content-block list we understand; pass through as-is

    try:
        parsed = [json.loads(t) for t in texts]
    except (TypeError, ValueError):
        return result  # not JSON text either; pass through

    if expect == "list":
        return parsed
    return parsed[0] if len(parsed) == 1 else parsed


class BankingMCPClient:
    """MCP client consumed through langchain-mcp-adapters over stdio."""

    def __init__(self, project_root: str | None = None, call_timeout: float | None = None):
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[1]
        self.client = None
        self.tools: dict[str, Any] = {}
        self.initialized = False
        self.resource_cache: dict[str, str] = {}
        self.call_timeout = call_timeout if call_timeout is not None else float(os.getenv("MCP_CALL_TIMEOUT_SECONDS", "20"))

    async def initialize(self):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self.client = MultiServerMCPClient(
            {
                "banking": {
                    "command": sys.executable,
                    "args": ["-m", "mcp_server.server"],
                    "transport": "stdio",
                    "env": {
                        **os.environ,
                        "PYTHONPATH": str(self.project_root),
                    },
                }
            }
        )
        tools = await self.client.get_tools(server_name="banking")
        self.tools = {tool.name: tool for tool in tools}
        self.initialized = True
        return self

    async def _invoke(self, name: str, arguments: dict[str, Any], expect: str | None = None):
        try:
            result = await asyncio.wait_for(self.tools[name].ainvoke(arguments), timeout=self.call_timeout)
        except asyncio.TimeoutError as exc:
            raise MCPToolTimeout(f"MCP tool '{name}' timed out after {self.call_timeout}s") from exc

        error_message = _looks_like_mcp_error_content(result)
        if error_message is not None:
            raise MCPToolError(f"MCP tool '{name}' failed: {error_message}")
        return parse_mcp_content(result, expect=expect)

    async def call(self, name: str, arguments: dict[str, Any], expect: str | None = None):
        """Call an MCP tool. `expect="list"` tells the result parser this tool's
        contract returns a list, so a single-item result isn't mistaken for a bare dict."""
        if not self.initialized:
            await self.initialize()
        if name not in self.tools:
            raise MCPClientError(f"missing MCP tool: {name}")

        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("transaction-dispute-copilot.mcp-client")
        except Exception:
            tracer = None

        if tracer is None:
            return await self._invoke(name, arguments, expect=expect)

        with tracer.start_as_current_span(
            f"tool.{name}",
            attributes={"tool.name": name, "mcp.transport": "stdio"},
        ) as span:
            try:
                result = await self._invoke(name, arguments, expect=expect)
                span.set_attribute("tool.status", "SUCCESS")
                return result
            except Exception as exc:
                span.set_attribute("tool.status", "ERROR")
                span.set_attribute("error.type", type(exc).__name__)
                raise

    async def resource(self, uri: str) -> str:
        if uri in self.resource_cache:
            return self.resource_cache[uri]
        if not self.initialized:
            await self.initialize()

        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("transaction-dispute-copilot.mcp-client")
        except Exception:
            tracer = None

        try:
            resources = await asyncio.wait_for(
                self.client.get_resources(server_name="banking", uris=[uri]),
                timeout=self.call_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise MCPToolTimeout(
                f"MCP resource '{uri}' timed out after {self.call_timeout}s"
            ) from exc
        if not resources:
            raise MCPClientError(f"missing MCP resource: {uri}")
        resource = resources[0]
        if hasattr(resource, "as_string"):
            value = resource.as_string()
        elif hasattr(resource, "text"):
            value = resource.text
        elif hasattr(resource, "data"):
            value = resource.data.decode() if isinstance(resource.data, bytes) else str(resource.data)
        else:
            value = str(resource)

        if tracer is not None:
            with tracer.start_as_current_span(
                "resource.chargeback_policy",
                attributes={"resource.uri": uri, "resource.content_length": len(value)},
            ):
                pass

        self.resource_cache[uri] = value
        return value
