from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def trace_id(span: dict[str, Any]) -> str | None:
    return span.get("context.trace_id") or span.get("trace_id")


def span_id(span: dict[str, Any]) -> str | None:
    return span.get("context.span_id") or span.get("span_id")


def run_id(span: dict[str, Any]) -> str | None:
    return (
        span.get("attributes.run_id")
        or span.get("run_id")
        or (span.get("attributes") or {}).get("run_id")
    )


def case_id(span: dict[str, Any]) -> str | None:
    return (
        span.get("attributes.case_id")
        or span.get("case_id")
        or (span.get("attributes") or {}).get("case_id")
    )


def span_latency_ms(span: dict[str, Any]) -> float:
    start = parse_iso(span.get("start_time"))
    end = parse_iso(span.get("end_time"))
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() * 1000.0)


def is_llm_span(span: dict[str, Any]) -> bool:
    kind = str(span.get("span_kind") or span.get("attributes.openinference.span.kind") or "").upper()
    if kind == "LLM":
        return True
    # Do not classify a parent application span as LLM merely because a custom
    # provider attribute is present. Require actual LLM model/token evidence.
    return bool(
        span.get("attributes.llm.model_name")
        or span.get("attributes.llm.token_count.total")
        or span.get("attributes.llm.token_count.prompt")
        or span.get("attributes.llm.token_count.completion")
    )


def is_root_copilot_span(span: dict[str, Any]) -> bool:
    return str(span.get("name") or "") == "copilot.run" and bool(run_id(span))


def usage(span: dict[str, Any]) -> dict[str, int]:
    direct = span.get("usage")
    if isinstance(direct, dict):
        return {
            "input_tokens": int(direct.get("input_tokens", direct.get("prompt_tokens", 0)) or 0),
            "output_tokens": int(direct.get("output_tokens", direct.get("completion_tokens", 0)) or 0),
            "total_tokens": int(direct.get("total_tokens", 0) or 0),
        }

    return {
        "input_tokens": int(span.get("attributes.llm.token_count.prompt", 0) or 0),
        "output_tokens": int(span.get("attributes.llm.token_count.completion", 0) or 0),
        "total_tokens": int(span.get("attributes.llm.token_count.total", 0) or 0),
    }


def provider(span: dict[str, Any]) -> str | None:
    value = (
        span.get("attributes.llm.provider")
        or span.get("llm.provider")
        or span.get("provider")
    )
    if value is None:
        return None
    value = str(value).lower()
    # Phoenix/OpenInference identifies Gemini through the Google backend name.
    return "gemini" if value == "google" else value


def model(span: dict[str, Any]) -> str | None:
    return (
        span.get("attributes.llm.model_name")
        or span.get("llm.model_name")
        or span.get("model")
    )


def span_index(spans: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        tid = trace_id(span)
        if tid:
            result.setdefault(tid, []).append(span)
    return result


def application_traces(spans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return application runs and attach orphaned Phoenix LLM spans by time window.

    Some LangChain/OpenInference integrations emit child LLM spans under a separate
    trace context when asynchronous execution crosses task boundaries. For those spans,
    Phoenix still gives us authoritative start/end times and model/token metadata, so we
    attach only LLM spans whose execution interval falls inside a single copilot.run root.
    DeepEval judge calls are excluded because they execute outside application run windows.
    """
    grouped = span_index(spans)
    result: dict[str, dict[str, Any]] = {}
    all_llm = [s for s in spans if is_llm_span(s)]
    for tid, group in grouped.items():
        roots = [s for s in group if is_root_copilot_span(s)]
        if not roots:
            continue
        root = sorted(roots, key=lambda s: parse_iso(s.get("start_time")) or datetime.min.replace(tzinfo=timezone.utc))[0]
        root_start = parse_iso(root.get("start_time"))
        root_end = parse_iso(root.get("end_time"))
        attached = list(group)
        if root_start and root_end:
            for llm_span in all_llm:
                # Do not duplicate LLM spans that are already in this root trace.
                if trace_id(llm_span) == tid:
                    continue
                llm_start = parse_iso(llm_span.get("start_time"))
                llm_end = parse_iso(llm_span.get("end_time"))
                if llm_start and llm_end and root_start <= llm_start and llm_end <= root_end:
                    attached.append(llm_span)
        result[tid] = {
            "trace_id": tid,
            "run_id": run_id(root),
            "case_id": case_id(root),
            "root": root,
            "spans": attached,
        }
    return result
