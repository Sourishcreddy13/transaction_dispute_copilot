from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import yaml

from _phoenix_evidence import application_traces, is_llm_span, model, provider, span_latency_ms, usage

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return round(ordered[index], 2)


def kind_for(span: dict) -> str:
    name = str(span.get("name") or "").lower()
    kind = str(span.get("span_kind") or span.get("attributes.openinference.span.kind") or "").upper()
    if kind == "TOOL" or name.startswith("tool.") or span.get("attributes.tool.name"):
        return "tool"
    if name == "copilot.run":
        return "end_to_end"
    if kind == "LLM" or is_llm_span(span):
        return "thinking"
    return "acting"


def load_quality() -> list[dict]:
    path = REPORTS / "deepeval_qualitative.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def load_eval() -> dict:
    path = REPORTS / "eval_report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quality_summary(items: list[dict]) -> tuple[dict[str, float | None], int, int]:
    keys = ("Answer Relevancy", "Faithfulness", "Hallucination")
    scored = [item for item in items if not item.get("skipped")]
    skipped = [item for item in items if item.get("skipped")]
    summary: dict[str, float | None] = {}
    for key in keys:
        vals = [float(item[key]["score"]) for item in scored if item.get(key, {}).get("score") is not None]
        summary[key] = round(sum(vals) / len(vals), 4) if vals else None
    return summary, len(scored), len(skipped)


spans = load_jsonl(ROOT / "traces" / "phoenix_spans.jsonl")
if not spans:
    raise RuntimeError("No Phoenix spans available.")

application = application_traces(spans)
if not application:
    raise RuntimeError("No copilot.run application traces were found in Phoenix export.")

application_spans = [span for trace in application.values() for span in trace["spans"]]
all_llm_spans = [span for span in spans if is_llm_span(span)]
if not all_llm_spans:
    raise RuntimeError("Phoenix export contains no LLM spans with token/provider telemetry.")

by_kind: dict[str, list[float]] = {"thinking": [], "acting": [], "tool": [], "end_to_end": []}
for span in application_spans:
    by_kind.setdefault(kind_for(span), []).append(span_latency_ms(span))

provider_config = yaml.safe_load((ROOT / "config/providers.yaml").read_text(encoding="utf-8")) or {}
provider_prices = {
    name: (
        float(cfg.get("pricing", {}).get("input_usd_per_1m_tokens", 0.0)),
        float(cfg.get("pricing", {}).get("output_usd_per_1m_tokens", 0.0)),
    )
    for name, cfg in provider_config.get("providers", {}).items()
}

input_tokens = 0
output_tokens = 0
cost_by_provider: dict[str, float] = {}
usage_by_provider: dict[str, dict[str, int]] = {}
for span in all_llm_spans:
    p = provider(span) or "unknown"
    u = usage(span)
    input_tokens += u["input_tokens"]
    output_tokens += u["output_tokens"]
    bucket = usage_by_provider.setdefault(p, {"input": 0, "output": 0, "total": 0, "calls": 0})
    bucket["input"] += u["input_tokens"]
    bucket["output"] += u["output_tokens"]
    bucket["total"] += u["total_tokens"]
    bucket["calls"] += 1
    in_price, out_price = provider_prices.get(p, (0.0, 0.0))
    cost_by_provider[p] = cost_by_provider.get(p, 0.0) + (
        u["input_tokens"] / 1_000_000 * in_price
        + u["output_tokens"] / 1_000_000 * out_price
    )

total_tokens = input_tokens + output_tokens
total_cost = round(sum(cost_by_provider.values()), 10)

provider_log = load_jsonl(ROOT / "logs" / "model_provider.jsonl")
primary = provider_config.get("primary", "gemini")
fallback_name = provider_config.get("fallback", "groq")
by_run: dict[str, list[dict]] = {}
for attempt in provider_log:
    rid = attempt.get("run_id")
    if rid:
        by_run.setdefault(rid, []).append(attempt)

fallback_activations = sum(
    any(a.get("provider") == fallback_name for a in attempts)
    for attempts in by_run.values()
)
fallback_successes = sum(
    any(a.get("provider") == fallback_name and a.get("status") == "SUCCESS" for a in attempts)
    for attempts in by_run.values()
)

quality, scored_cases, skipped_cases = quality_summary(load_quality())
eval_report = load_eval().get("deterministic_evaluation", {})
trace_sha = __import__("hashlib").sha256(
    (ROOT / "traces" / "phoenix_spans.jsonl").read_bytes()
).hexdigest()

report = {
    "source": "Phoenix span export + machine-generated provider attempts + deterministic evaluation",
    "source_artifact": "traces/phoenix_spans.jsonl",
    "source_sha256": trace_sha,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "telemetry_scope": {
        "latency": "application traces containing copilot.run",
        "tokens_and_cost": "all LLM spans in the evidence time window, including evaluation/DeepEval judge calls because Phoenix does not currently correlate those child LLM spans to copilot.run",
        "provider_fallback": "machine-generated provider attempts from the fresh evidence run only",
    },
    "runs": len(application),
    "span_count": len(application_spans),
    "llm_span_count": len(all_llm_spans),
    "latency_ms": {
        "overall": {
            "p50": percentile([span_latency_ms(s) for s in application_spans], 0.50),
            "p95": percentile([span_latency_ms(s) for s in application_spans], 0.95),
            "p99": percentile([span_latency_ms(s) for s in application_spans], 0.99),
        },
        **{
            kind: {
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "p99": percentile(values, 0.99),
            }
            for kind, values in by_kind.items()
        },
    },
    "tokens": {
        "input": input_tokens,
        "output": output_tokens,
        "total": total_tokens,
        "by_provider": usage_by_provider,
    },
    "cost": {
        "estimated_usd": total_cost,
        "by_provider": {k: round(v, 10) for k, v in sorted(cost_by_provider.items())},
        "pricing_basis": {
            name: {
                "input_usd_per_1m_tokens": values[0],
                "output_usd_per_1m_tokens": values[1],
            }
            for name, values in provider_prices.items()
        },
        "basis_date": "2026-09-28",
    },
    "provider": {
        "primary": primary,
        "fallback": fallback_name,
        "attempt_count": len(provider_log),
        "run_count": len(by_run),
        "fallback_activations": fallback_activations,
        "fallback_successes": fallback_successes,
        "fallback_rate": fallback_activations / len(by_run) if by_run else 0.0,
    },
    "accuracy": {
        "action": eval_report.get("action_accuracy"),
        "review": eval_report.get("review_accuracy"),
        "intent": eval_report.get("intent_accuracy"),
    },
    "qualitative_eval": {
        "scored_cases": scored_cases,
        "skipped_cases": skipped_cases,
        "metrics": quality,
    },
    "quality_rates": {
        "faithfulness_pass_rate": (
            sum(float(item["Faithfulness"]["score"]) >= 0.8 for item in load_quality() if item.get("Faithfulness")) / scored_cases
            if scored_cases else None
        ),
        "answer_relevancy_pass_rate": (
            sum(float(item["Answer Relevancy"]["score"]) >= 0.8 for item in load_quality() if item.get("Answer Relevancy")) / scored_cases
            if scored_cases else None
        ),
        "hallucination_rate": (
            1.0 - quality["Hallucination"] if quality["Hallucination"] is not None else None
        ),
    },
}

(REPORTS / "golden_signals.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(REPORTS / "golden_signals.json")
