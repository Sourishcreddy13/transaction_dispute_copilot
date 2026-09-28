from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.data_plane import DataPlane
from app.core.security import verify_context
from app.models import AccessContext

mcp = FastMCP("transaction-dispute-banking-data-plane")
data = DataPlane()


def _ctx(token: str, permission: str) -> AccessContext:
    return verify_context(
        token,
        os.environ.get("ACCESS_SECRET", "dev-only-change-me"),
        permission,
        audience="mcp",
    )


def _safe(value: Any):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


def _log(tool: str, args: dict, result: object, started: float, status: str = "SUCCESS"):
    Path("logs").mkdir(exist_ok=True)
    safe_args = {k: ("<AUTH_CONTEXT>" if k == "access_context" else v) for k, v in args.items()}
    row = {
        "timestamp": time.time(),
        "agent": "mcp_server",
        "tool_name": tool,
        "args": safe_args,
        "result": _safe(result),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": status,
    }
    with Path("logs/tool_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    with Path("logs/mcp_transcript.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _call(tool, fn, args):
    started = time.perf_counter()
    try:
        result = fn()
        _log(tool, args, result, started)
        return result
    except Exception as exc:
        _log(tool, args, {"error": str(exc)}, started, "ERROR")
        raise


@mcp.tool()
def get_transaction(access_context: str, transaction_id: str) -> dict:
    ctx = _ctx(access_context, "txn:read")
    t = _call("get_transaction", lambda: data.get_transaction(ctx, transaction_id), {
        "access_context": access_context,
        "transaction_id": transaction_id,
    })
    return t.model_dump(mode="json")


@mcp.tool()
def get_recent_transactions(access_context: str, window_days: int = 90, limit: int = 20, as_of: str | None = None) -> list[dict]:
    ctx = _ctx(access_context, "history:read")
    xs = _call("get_recent_transactions", lambda: data.get_recent_transactions(ctx, window_days, limit, as_of), {
        "access_context": access_context,
        "window_days": window_days,
        "limit": limit,
        "as_of": as_of,
    })
    return [x.model_dump(mode="json") for x in xs]


@mcp.tool()
def get_customer_profile(access_context: str) -> dict:
    ctx = _ctx(access_context, "profile:read")
    p = _call("get_customer_profile", lambda: data.get_customer_profile(ctx), {
        "access_context": access_context,
    })
    return p.model_dump(mode="json")


@mcp.tool()
def get_prior_disputes(access_context: str, limit: int = 10) -> list[dict]:
    ctx = _ctx(access_context, "history:read")
    xs = _call("get_prior_disputes", lambda: data.get_prior_disputes(ctx, limit), {
        "access_context": access_context,
        "limit": limit,
    })
    return [x.model_dump(mode="json") for x in xs]


@mcp.tool()
def get_account_summary(access_context: str) -> dict:
    ctx = _ctx(access_context, "account:read")
    account, card = _call(
        "get_account_summary",
        lambda: (data.accounts[ctx.customer_id], data.cards[ctx.customer_id]),
        {"access_context": access_context},
    )
    return {
        "account": account.model_dump(mode="json"),
        "card": card.model_dump(mode="json"),
    }


@mcp.tool()
def get_statements(access_context: str, limit: int = 12) -> list[dict]:
    ctx = _ctx(access_context, "account:read")
    account = data.accounts[ctx.customer_id]
    statements = _call(
        "get_statements",
        lambda: data.statements.get(account.account_id, [])[:limit],
        {"access_context": access_context, "limit": limit},
    )
    return [x.model_dump(mode="json") for x in statements]


@mcp.resource("policy://chargeback/manual")
def chargeback_policy_resource() -> str:
    started = time.perf_counter()
    root = Path("data/policy_corpus")
    docs = []
    for p in sorted(root.glob("*.md")):
        docs.append(f"# {p.name}\n{p.read_text(encoding='utf-8')}")
    result = "\n\n".join(docs)
    _log(
        "resource:policy://chargeback/manual",
        {"uri": "policy://chargeback/manual"},
        {"document_count": len(docs), "content_length": len(result)},
        started,
    )
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
