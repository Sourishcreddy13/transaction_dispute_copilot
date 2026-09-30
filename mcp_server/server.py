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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
MAX_WINDOW_DAYS = 365
MAX_LIST_LIMIT = 100
MAX_RESOURCE_BYTES = 2_000_000

mcp = FastMCP("transaction-dispute-banking-data-plane")
data = DataPlane()


def _secret() -> str:
    value = os.environ.get("ACCESS_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("ACCESS_SECRET_TOO_WEAK")
    return value


def _ctx(token: str, permission: str) -> AccessContext:
    return verify_context(token, _secret(), permission, audience="mcp")


def _safe(value: Any):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_safe(v) for v in value[:MAX_LIST_LIMIT]]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


def _sanitize_error(exc: Exception) -> str:
    return type(exc).__name__.upper() + "_FAILED"


def _redact(value: Any):
    if isinstance(value, dict):
        return {k: ("<REDACTED>" if k == "access_context" else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value[:MAX_LIST_LIMIT]]
    if isinstance(value, str) and len(value) > 1000:
        return value[:997] + "..."
    return value


def _log(tool: str, args: dict, result: object, started: float, status: str = "SUCCESS"):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": time.time(),
        "agent": "mcp_server",
        "tool_name": tool,
        "args": _redact(args),
        "result": _redact(_safe(result)),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "status": status,
    }
    for filename in ("tool_calls.jsonl", "mcp_transcript.jsonl"):
        with (LOG_DIR / filename).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def _call(tool, fn, args):
    started = time.perf_counter()
    try:
        result = fn()
        _log(tool, args, result, started)
        return result
    except Exception as exc:  # noqa: BLE001
        _log(tool, args, {"error_code": _sanitize_error(exc)}, started, "ERROR")
        raise


def _limit(value: int, default: int = 20) -> int:
    value = default if value is None else int(value)
    if value < 1 or value > MAX_LIST_LIMIT:
        raise ValueError("LIMIT_OUT_OF_RANGE")
    return value


@mcp.tool()
def get_transaction(access_context: str, transaction_id: str) -> dict:
    ctx = _ctx(access_context, "txn:read")
    if len(transaction_id) > 100:
        raise ValueError("TRANSACTION_ID_INVALID")
    t = _call("get_transaction", lambda: data.get_transaction(ctx, transaction_id), {"access_context": access_context, "transaction_id": transaction_id})
    return t.model_dump(mode="json")


@mcp.tool()
def get_recent_transactions(access_context: str, window_days: int = 90, limit: int = 20, as_of: str | None = None) -> list[dict]:
    ctx = _ctx(access_context, "history:read")
    if window_days < 0 or window_days > MAX_WINDOW_DAYS:
        raise ValueError("WINDOW_DAYS_OUT_OF_RANGE")
    limit = _limit(limit)
    xs = _call("get_recent_transactions", lambda: data.get_recent_transactions(ctx, window_days, limit, as_of), {"access_context": access_context, "window_days": window_days, "limit": limit, "as_of": as_of})
    return [x.model_dump(mode="json") for x in xs]


@mcp.tool()
def get_customer_profile(access_context: str) -> dict:
    ctx = _ctx(access_context, "profile:read")
    p = _call("get_customer_profile", lambda: data.get_customer_profile(ctx), {"access_context": access_context})
    return p.model_dump(mode="json")


@mcp.tool()
def get_prior_disputes(access_context: str, limit: int = 10) -> list[dict]:
    ctx = _ctx(access_context, "history:read")
    limit = _limit(limit)
    xs = _call("get_prior_disputes", lambda: data.get_prior_disputes(ctx, limit), {"access_context": access_context, "limit": limit})
    return [x.model_dump(mode="json") for x in xs]


@mcp.tool()
def get_account_summary(access_context: str) -> dict:
    ctx = _ctx(access_context, "account:read")
    account, card = _call("get_account_summary", lambda: (data.accounts[ctx.customer_id], data.cards[ctx.customer_id]), {"access_context": access_context})
    return {"account": account.model_dump(mode="json"), "card": card.model_dump(mode="json")}


@mcp.tool()
def get_statements(access_context: str, limit: int = 12) -> list[dict]:
    ctx = _ctx(access_context, "account:read")
    limit = _limit(limit)
    account = data.accounts[ctx.customer_id]
    statements = _call("get_statements", lambda: data.statements.get(account.account_id, [])[:limit], {"access_context": access_context, "limit": limit})
    return [x.model_dump(mode="json") for x in statements]


@mcp.resource("policy://chargeback/manual")
def chargeback_policy_resource() -> str:
    started = time.perf_counter()
    root = PROJECT_ROOT / "data" / "policy_corpus"
    docs = []
    for p in sorted(root.glob("*.md")):
        docs.append(f"# {p.name}\n{p.read_text(encoding='utf-8')}")
    result = "\n\n".join(docs)
    if len(result.encode()) > MAX_RESOURCE_BYTES:
        raise RuntimeError("POLICY_RESOURCE_TOO_LARGE")
    _log("resource:policy://chargeback/manual", {"uri": "policy://chargeback/manual"}, {"document_count": len(docs), "content_length": len(result)}, started)
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
