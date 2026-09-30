from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


class MCPClientError(RuntimeError):
    pass


class MCPResponseContractError(MCPClientError, ValueError):
    """Raised when an MCP response violates the declared client contract."""


class MCPToolTimeout(MCPClientError):
    pass


class MCPToolError(MCPClientError):
    pass


_MAX_RESULT_BYTES = 2_000_000


def _block_text(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("text")
    return getattr(block, "text", None)


def _looks_like_mcp_error_content(result: Any) -> str | None:
    if isinstance(result, list) and result:
        text = _block_text(result[0])
        if isinstance(text, str) and ("error executing tool" in text.lower() or text.lower().startswith("error")):
            return text[:500]
    return None


def parse_mcp_content(result: Any, expect: str | None = None) -> Any:
    if not isinstance(result, list):
        if len(json.dumps(result, default=str).encode()) > _MAX_RESULT_BYTES:
            raise MCPClientError("MCP_RESULT_TOO_LARGE")
        return result
    if not result:
        return [] if expect == "list" else result

    texts = [_block_text(b) for b in result]
    if any(t is None or not isinstance(t, str) for t in texts):
        raise MCPResponseContractError("MCP_RESPONSE_CONTRACT_INVALID")
    try:
        parsed = [json.loads(t) for t in texts]
    except (TypeError, ValueError) as exc:
        raise MCPResponseContractError("MCP_RESPONSE_JSON_INVALID") from exc

    if len(json.dumps(parsed, default=str).encode()) > _MAX_RESULT_BYTES:
        raise MCPClientError("MCP_RESULT_TOO_LARGE")
    if expect == "list":
        if not all(isinstance(item, (dict, list, str, int, float, bool, type(None))) for item in parsed):
            raise MCPResponseContractError("MCP_LIST_CONTRACT_INVALID")
        return parsed
    if expect == "dict" and len(parsed) != 1:
        raise MCPResponseContractError("MCP_DICT_CONTRACT_INVALID")
    value = parsed[0] if len(parsed) == 1 else parsed
    if expect == "dict" and not isinstance(value, dict):
        raise MCPResponseContractError("MCP_DICT_CONTRACT_INVALID")
    return value


class BankingMCPClient:
    """MCP client consumed through langchain-mcp-adapters over stdio."""

    REQUIRED_TOOLS = {
        "get_transaction",
        "get_recent_transactions",
        "get_customer_profile",
        "get_prior_disputes",
        "get_account_summary",
        "get_statements",
    }

    def __init__(self, project_root: str | None = None, call_timeout: float | None = None):
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[1]
        self.client = None
        self.tools: dict[str, Any] = {}
        self.initialized = False
        self.resource_cache: dict[str, str] = {}
        self.call_timeout = max(1.0, min(call_timeout if call_timeout is not None else float(os.getenv("MCP_CALL_TIMEOUT_SECONDS", "20")), 120.0))
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        async with self._init_lock:
            if self.initialized:
                return self
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                self.client = MultiServerMCPClient(
                    {
                        "banking": {
                            "command": sys.executable,
                            "args": ["-m", "mcp_server.server"],
                            "transport": "stdio",
                            "env": {**os.environ, "PYTHONPATH": str(self.project_root)},
                        }
                    }
                )
                tools = await asyncio.wait_for(self.client.get_tools(server_name="banking"), timeout=self.call_timeout)
                self.tools = {tool.name: tool for tool in tools}
                missing = self.REQUIRED_TOOLS - self.tools.keys()
                if missing:
                    raise MCPClientError("MCP_REQUIRED_TOOLS_MISSING")
                self.initialized = True
                return self
            except asyncio.TimeoutError as exc:
                self.initialized = False
                raise MCPToolTimeout("MCP_INITIALIZATION_TIMEOUT") from exc
            except Exception as exc:  # noqa: BLE001
                self.initialized = False
                raise MCPClientError(f"MCP_INITIALIZATION_FAILED:{type(exc).__name__}") from exc

    async def _invoke(self, name: str, arguments: dict[str, Any], expect: str | None = None):
        if name not in self.tools:
            raise MCPClientError("MCP_TOOL_NOT_AVAILABLE")
        try:
            result = await asyncio.wait_for(self.tools[name].ainvoke(arguments), timeout=self.call_timeout)
        except asyncio.TimeoutError as exc:
            raise MCPToolTimeout(f"MCP tool '{name}' timed out") from exc
        error_message = _looks_like_mcp_error_content(result)
        if error_message is not None:
            raise MCPToolError(f"MCP tool '{name}' failed")
        return parse_mcp_content(result, expect=expect)

    async def call(self, name: str, arguments: dict[str, Any], expect: str | None = None):
        if not self.initialized:
            await self.initialize()
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("transaction-dispute-copilot.mcp-client")
        except Exception:
            tracer = None
        if tracer is None:
            return await self._invoke(name, arguments, expect=expect)
        with tracer.start_as_current_span(
            f"tool.{name}", attributes={"tool.name": name, "mcp.transport": "stdio"}
        ) as span:
            try:
                result = await self._invoke(name, arguments, expect=expect)
                span.set_attribute("tool.status", "SUCCESS")
                return result
            except Exception as exc:  # noqa: BLE001
                span.set_attribute("tool.status", "ERROR")
                span.set_attribute("error.type", type(exc).__name__)
                raise

    async def resource(self, uri: str) -> str:
        if len(uri) > 300 or not uri.startswith("policy://"):
            raise MCPClientError("MCP_RESOURCE_URI_INVALID")
        if uri in self.resource_cache:
            return self.resource_cache[uri]
        if not self.initialized:
            await self.initialize()
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("transaction-dispute-copilot.mcp-client")
        except Exception:
            tracer = None

        started = tracer.start_as_current_span("resource.chargeback_policy") if tracer else None
        if started:
            span_cm = started
        else:
            from contextlib import nullcontext
            span_cm = nullcontext(None)
        with span_cm as span:
            try:
                resources = await asyncio.wait_for(
                    self.client.get_resources(server_name="banking", uris=[uri]), timeout=self.call_timeout
                )
                if not resources:
                    raise MCPClientError("MCP_RESOURCE_NOT_FOUND")
                resource = resources[0]
                if hasattr(resource, "as_string"):
                    value = resource.as_string()
                elif hasattr(resource, "text"):
                    value = resource.text
                elif hasattr(resource, "data"):
                    value = resource.data.decode() if isinstance(resource.data, bytes) else str(resource.data)
                else:
                    value = str(resource)
                if not isinstance(value, str) or len(value.encode()) > _MAX_RESULT_BYTES:
                    raise MCPClientError("MCP_RESOURCE_TOO_LARGE")
                if span is not None:
                    span.set_attribute("resource.uri", uri)
                    span.set_attribute("resource.content_length", len(value))
                    span.set_attribute("resource.status", "SUCCESS")
                self.resource_cache[uri] = value
                return value
            except asyncio.TimeoutError as exc:
                if span is not None:
                    span.set_attribute("resource.status", "TIMEOUT")
                raise MCPToolTimeout("MCP_RESOURCE_TIMEOUT") from exc
            except Exception:
                if span is not None:
                    span.set_attribute("resource.status", "ERROR")
                raise
