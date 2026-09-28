from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import yaml


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # Phoenix/OTel exports RFC3339 with a trailing "Z"; datetime.fromisoformat
        # (py<3.11) doesn't accept "Z" directly.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _span_latency_ms(span: dict) -> float:
    """Real Phoenix/OpenInference export schema carries start_time/end_time
    (ISO-8601 strings), NOT a top-level latency_ms field. Compute it here so the
    golden-signals report reflects actual measured span duration instead of a
    key that was never populated by the exporter (see docs/deviations.md D-03)."""
    start = _parse_iso(span.get("start_time"))
    end = _parse_iso(span.get("end_time"))
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() * 1000.0)


def _span_run_id(span: dict) -> str | None:
    """Real export nests custom attributes under dotted keys, e.g.
    "attributes.run_id", not a top-level "run_id"."""
    return span.get("attributes.run_id") or span.get("run_id") or (span.get("attributes") or {}).get("run_id")


def _span_usage(span: dict) -> dict:
    """Token usage lives under attributes.llm.token_count.* in the real export,
    not under a top-level "usage" object."""
    if span.get("usage"):
        return span["usage"]
    return {
        "input_tokens": span.get("attributes.llm.token_count.prompt", 0) or 0,
        "output_tokens": span.get("attributes.llm.token_count.completion", 0) or 0,
        "total_tokens": span.get("attributes.llm.token_count.total", 0) or 0,
    }

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



def _span_kind(span: dict) -> str:
    name = span.get("name")
    oi_kind = (span.get("attributes.openinference.span.kind") or span.get("span_kind") or "").upper()
    text = (name or "").lower()
    if text.startswith("tool.") or oi_kind == "TOOL":
        return "tool"
    if text.startswith("copilot.run"):
        return "end_to_end"
    if text.startswith("agent.") or text.startswith("copilot.review"):
        return "acting"
    if oi_kind == "LLM" or any(x in text for x in ("llm", "chat", "generate", "gemini", "groq")):
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
    run_id = _span_run_id(span)
    provider = provider_by_run.get(run_id, {})
    usage = _span_usage(span)
    rows.append(
        {
            "name": span.get("name"),
            "kind": _span_kind(span),
            "run_id": run_id,
            "case_id": span.get("attributes.case_id") or span.get("case_id"),
            "trace_id": span.get("context.trace_id") or span.get("trace_id"),
            "span_id": span.get("context.span_id") or span.get("span_id"),
            "latency_ms": _span_latency_ms(span),
            "provider": provider.get("provider") or span.get("attributes.llm.provider") or span.get("llm.provider"),
            "provider_status": provider.get("status"),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
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
run_count = len({r.get("run_id") for r in rows if r.get("run_id")})

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


# Sanity check: this report exists specifically to prove Phoenix-derived latency
# is being measured (AC-09). If the span schema drifts again in a future export
# and every latency bucket comes back empty, fail loudly here instead of quietly
# committing a report full of zeros.
if spans and report["runs"] == 0:
    raise RuntimeError(
        "golden_signals: found %d Phoenix spans but resolved 0 distinct run_ids -- "
        "check that traces/phoenix_spans.jsonl's schema still matches "
        "_span_run_id()/_span_latency_ms() in this script." % len(spans)
    )
if spans and all(v is None for v in report["latency_ms"]["overall"].values()):
    raise RuntimeError(
        "golden_signals: found %d Phoenix spans but computed no latency samples -- "
        "check start_time/end_time parsing in _span_latency_ms()." % len(spans)
    )

(REPORTS / "golden_signals.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(REPORTS / "golden_signals.json")
