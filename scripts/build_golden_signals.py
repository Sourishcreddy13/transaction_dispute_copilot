from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result



def _span_kind(name: str | None) -> str:
    text = (name or "").lower()
    if text.startswith("tool."):
        return "tool"
    if text.startswith("copilot.run"):
        return "end_to_end"
    if text.startswith("agent.") or text.startswith("copilot.review"):
        return "acting"
    if any(x in text for x in ("llm", "chat", "generate", "gemini", "groq")):
        return "thinking"
    return "acting"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return round(ordered[idx], 2)


def _kind_percentiles(values: list[float]) -> dict[str, float | None]:
    return {"p50": percentile(values, .50), "p95": percentile(values, .95), "p99": percentile(values, .99)}


def _pass_rate(items: list[dict], name: str, threshold: float) -> float | None:
    vals = [float(item[name]["score"]) for item in items if item.get(name, {}).get("score") is not None]
    return sum(v >= threshold for v in vals) / len(vals) if vals else None


def _error_rate_from_aligned_score(items: list[dict], name: str) -> float | None:
    vals = [float(item[name]["score"]) for item in items if item.get(name, {}).get("score") is not None]
    return 1.0 - sum(vals) / len(vals) if vals else None


spans = load_jsonl(ROOT / "traces" / "phoenix_spans.jsonl")
providers = load_jsonl(ROOT / "logs" / "model_provider.jsonl")
qual_path = REPORTS / "deepeval_qualitative.json"
eval_path = REPORTS / "eval_report.json"
qualitative_cases = json.loads(qual_path.read_text()) if qual_path.exists() else []
eval_report = json.loads(eval_path.read_text()) if eval_path.exists() else {}
provider_config = yaml.safe_load((ROOT / "config/providers.yaml").read_text()) or {}
pricing = provider_config.get("pricing", {})

provider_by_run: dict[str, dict] = {}
for row in providers:
    if row.get("run_id"):
        provider_by_run[row["run_id"]] = row

# Turn Phoenix/provider records into one evidence table.
rows: list[dict] = []
for span in spans:
    run_id = span.get("run_id")
    provider = provider_by_run.get(run_id, {})
    usage = span.get("usage") or {}
    rows.append(
        {
            "name": span.get("name"),
            "kind": span.get("kind") or _span_kind(span.get("name")),
            "run_id": run_id,
            "case_id": span.get("case_id"),
            "trace_id": span.get("trace_id"),
            "span_id": span.get("span_id"),
            "latency_ms": span.get("latency_ms", 0),
            "provider": provider.get("provider") or span.get("llm.provider"),
            "provider_status": provider.get("status"),
            "input_tokens": usage.get("input_tokens", span.get("llm.input_tokens", 0)) or 0,
            "output_tokens": usage.get("output_tokens", span.get("llm.output_tokens", 0)) or 0,
            "total_tokens": usage.get("total_tokens", span.get("llm.total_tokens", 0)) or 0,
            "estimated_cost_usd": span.get("estimated_cost_usd", 0) or 0,
        }
    )

fields = list(rows[0].keys()) if rows else [
    "name", "kind", "run_id", "case_id", "trace_id", "span_id", "latency_ms",
    "provider", "provider_status", "input_tokens", "output_tokens", "total_tokens",
    "estimated_cost_usd",
]
with (REPORTS / "dashboard_data.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

latencies = [float(x["latency_ms"]) for x in rows if x.get("latency_ms") is not None]
by_kind: dict[str, list[float]] = {}
for row in rows:
    by_kind.setdefault(row["kind"], []).append(float(row["latency_ms"] or 0))

input_tokens = sum(int(x.get("input_tokens") or 0) for x in rows)
output_tokens = sum(int(x.get("output_tokens") or 0) for x in rows)
if not input_tokens and not output_tokens:
    for provider in providers:
        usage = provider.get("usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)

total_tokens = input_tokens + output_tokens
input_price = float(pricing.get("input_usd_per_1k_tokens", 0.0))
output_price = float(pricing.get("output_usd_per_1k_tokens", 0.0))
estimated_cost = input_tokens / 1000 * input_price + output_tokens / 1000 * output_price

fallbacks = sum(1 for x in providers if x.get("status") == "SUCCESS" and int(x.get("attempt", 1)) > 1)
run_count = len({x.get("run_id") for x in spans if x.get("run_id")})

deterministic = eval_report.get("deterministic_evaluation", {})
quality_scores: dict[str, float | None] = {}
for metric_name in ("Answer Relevancy", "Faithfulness", "Hallucination"):
    vals = [
        float(item[metric_name]["score"])
        for item in qualitative_cases
        if item.get(metric_name, {}).get("score") is not None
    ]
    quality_scores[metric_name] = sum(vals) / len(vals) if vals else None

report = {
    "source": "Phoenix span export + machine-generated provider attempts + deterministic evaluation",
    "runs": run_count,
    "span_count": len(spans),
    "latency_ms": {
        "overall": {"p50": percentile(latencies, .50), "p95": percentile(latencies, .95), "p99": percentile(latencies, .99)},
        "thinking": _kind_percentiles(by_kind.get("thinking", [])),
        "acting": _kind_percentiles(by_kind.get("acting", [])),
        "tool": _kind_percentiles(by_kind.get("tool", [])),
        "end_to_end": _kind_percentiles(by_kind.get("end_to_end", [])),
    },
    "tokens": {"input": input_tokens, "output": output_tokens, "total": total_tokens},
    "cost": {
        "estimated_usd": round(estimated_cost, 8),
        "pricing_basis": {
            "input_usd_per_1k_tokens": input_price,
            "output_usd_per_1k_tokens": output_price,
        },
    },
    "provider": {
        "attempt_count": len(providers),
        "fallback_activations": fallbacks,
        "fallback_rate": fallbacks / len(providers) if providers else 0.0,
    },
    "accuracy": {
        "action": deterministic.get("action_accuracy"),
        "review": deterministic.get("review_accuracy"),
        "intent": deterministic.get("intent_accuracy"),
    },
    "qualitative_eval": quality_scores,
    "quality_rates": {
        "faithfulness_pass_rate": _pass_rate(qualitative_cases, "Faithfulness", .8),
        "answer_relevancy_pass_rate": _pass_rate(qualitative_cases, "Answer Relevancy", .8),
        "hallucination_rate": _error_rate_from_aligned_score(qualitative_cases, "Hallucination"),
    },
}

(REPORTS / "golden_signals.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(REPORTS / "golden_signals.json")
