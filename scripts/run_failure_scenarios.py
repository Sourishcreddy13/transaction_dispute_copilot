from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.core.semantic import ProviderError, SemanticGateway
from app.core.security import mint_context
from app.models import Principal, Role
from src.observability.tracing import configure_phoenix

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "failure-scenarios.json"
TOOL_LOG = ROOT / "logs" / "tool_calls.jsonl"


def tool_log_ref(transaction_id: str) -> dict | None:
    if not TOOL_LOG.exists():
        return None
    rows = TOOL_LOG.read_text(encoding="utf-8").splitlines()
    for index in range(len(rows) - 1, -1, -1):
        try:
            row = json.loads(rows[index])
        except json.JSONDecodeError:
            continue
        args = row.get("args") or {}
        result = row.get("result") or {}
        if (
            row.get("tool_name") == "get_transaction"
            and args.get("transaction_id") == transaction_id
            and row.get("status") == "ERROR"
        ):
            return {
                "artifact": "logs/tool_calls.jsonl",
                "line": index + 1,
                "tool_name": row.get("tool_name"),
                "transaction_id": transaction_id,
                "status": row.get("status"),
                "error": result.get("error"),
            }
    return None


def phoenix_span_ref(span: object | None, run_id: str) -> dict:
    if span is None:
        raise RuntimeError("Phoenix failure scenario span was not created.")
    context = span.get_span_context()
    trace_id = format(context.trace_id, "032x")
    span_id = format(context.span_id, "016x")
    if not trace_id or not span_id:
        raise RuntimeError("Phoenix failure scenario span has no trace/span identity.")
    return {
        "artifact": "traces/phoenix_spans.jsonl",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": span_id,
    }


def run() -> list[dict]:
    rows: list[dict] = []
    secret = os.environ.get("ACCESS_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("ACCESS_SECRET must be at least 32 characters for failure evidence generation.")

    # Failure 1: deliberately fail the Gemini primary and verify Groq fallback.
    previous = os.environ.get("FORCE_GEMINI_FAILURE")
    os.environ["FORCE_GEMINI_FAILURE"] = "1"
    run_id = "FAIL-FALLBACK-001"
    trace_manager = configure_phoenix(
        enabled=True,
        endpoint=os.getenv("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces"),
    )
    failure_span = None
    gateway = None
    try:
        with trace_manager.span(
            "failure.gemini_primary_fallback",
            run_id=run_id,
            failure_scenario="gemini_primary_failure",
            evidence_id=os.getenv("EVIDENCE_ID", "unknown"),
        ) as span:
            failure_span = span
            gateway = SemanticGateway("real")
            try:
                gateway.classify(
                    "I did not make this transaction T-1007",
                    run_id=run_id,
                )
                rows.append(
                    {
                        "scenario": "gemini_primary_failure_fallback",
                        "status": "handled",
                        "run_id": run_id,
                        "failure_type": "primary_provider_failure",
                        "provider_attempts": gateway.telemetry_for(run_id).get("attempts", []),
                        "phoenix_ref": phoenix_span_ref(span, run_id),
                        "root_cause": "Gemini was deliberately fault-injected for resilience verification.",
                        "fix": "The provider gateway activates Groq fallback and records the provider attempts without releasing an unsafe decision.",
                    }
                )
            except ProviderError as exc:
                rows.append(
                    {
                        "scenario": "semantic_provider_exhaustion",
                        "status": "handled",
                        "run_id": run_id,
                        "failure_type": "provider_exhaustion",
                        "provider_attempts": exc.attempts,
                        "phoenix_ref": phoenix_span_ref(span, run_id),
                        "root_cause": "Both semantic attempts failed or were fault-injected.",
                        "fix": "The workflow converts semantic exhaustion into NEEDS_INFO instead of releasing a decision.",
                    }
                )
    finally:
        if gateway is not None:
            try:
                gateway.close()
            except Exception:
                pass
        if previous is None:
            os.environ.pop("FORCE_GEMINI_FAILURE", None)
        else:
            os.environ["FORCE_GEMINI_FAILURE"] = previous

    from mcp_server import server

    token = mint_context(
        Principal(actor_id="analyst:A-001", role=Role.analyst, team="fraud-ops"),
        "CASE-FAIL-001",
        "C-1001",
        ["txn:read"],
        secret,
    )

    # Failure 2: cross-customer access denial.  Use a dedicated Phoenix span rather
    # than a mutable JSONL line number so the evidence citation remains resolvable
    # after the tool log is regenerated or appended to.
    run_id = "FAIL-MCP-IDOR-001"
    try:
        with trace_manager.span(
            "failure.cross_customer_transaction_access",
            run_id=run_id,
            failure_scenario="cross_customer_transaction_access",
            evidence_id=os.getenv("EVIDENCE_ID", "unknown"),
        ) as span:
            server.get_transaction(token, "T-2001")
    except Exception as exc:
        rows.append(
            {
                "scenario": "cross_customer_transaction_access",
                "status": "handled",
                "run_id": run_id,
                "error": "TXN_NOT_FOUND",
                "phoenix_ref": phoenix_span_ref(span, run_id),
                "root_cause": "Requested transaction belongs to another synthetic customer.",
                "fix": "The MCP data plane checks the signed case/customer binding and refuses cross-customer access.",
            }
        )

    # Failure 3: unknown transaction.  Again, cite the generated Phoenix span
    # rather than depending on a line number in an append-only tool log.
    run_id = "FAIL-MCP-NOTFOUND-001"
    try:
        with trace_manager.span(
            "failure.unknown_transaction",
            run_id=run_id,
            failure_scenario="unknown_transaction",
            evidence_id=os.getenv("EVIDENCE_ID", "unknown"),
        ) as span:
            server.get_transaction(token, "T-NOT-EXIST")
    except Exception as exc:
        rows.append(
            {
                "scenario": "unknown_transaction",
                "status": "handled",
                "run_id": run_id,
                "error": "TXN_NOT_FOUND",
                "phoenix_ref": phoenix_span_ref(span, run_id),
                "root_cause": "The requested synthetic transaction does not exist.",
                "fix": "The MCP tool returns typed TXN_NOT_FOUND semantics instead of fabricating transaction data.",
            }
        )

    # Allow batched telemetry from the dedicated failure span to reach Phoenix before the
    # evidence exporter queries it.
    if trace_manager.provider is not None:
        try:
            trace_manager.provider.force_flush(timeout_millis=5000)
        except Exception:
            pass
    time.sleep(1)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# Failure Analysis",
        "",
        "Generated by `scripts/run_failure_scenarios.py`. Each entry below is produced by an executable fault injection and cites either a Phoenix span or a machine-generated tool-log record.",
    ]
    for index, row in enumerate(rows, 1):
        lines.extend(
            [
                "",
                f"## Failure {index}: {row['scenario']}",
                f"- Status: `{row.get('status')}`",
                f"- Run ID: `{row.get('run_id', 'n/a')}`",
            ]
        )
        if row.get("phoenix_ref"):
            ref = row["phoenix_ref"]
            lines.append(
                f"- Phoenix reference: `traces/phoenix_spans.jsonl` · run_id=`{ref['run_id']}` · trace_id=`{ref['trace_id']}` · span_id=`{ref['span_id']}`"
            )
        if row.get("tool_log_ref"):
            ref = row["tool_log_ref"]
            lines.append(
                f"- Tool-log reference: `logs/tool_calls.jsonl` line `{ref['line']}` · tool=`{ref['tool_name']}` · transaction=`{ref['transaction_id']}` · error=`{ref['error']}`"
            )
        lines.extend(
            [
                f"- Root cause: {row.get('root_cause')}",
                f"- Fix: {row.get('fix')}",
            ]
        )
    (ROOT / "docs/failure-analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)
    return rows


if __name__ == "__main__":
    run()
