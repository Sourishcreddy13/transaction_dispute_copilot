from __future__ import annotations

import csv
import hashlib
import json
import statistics
from pathlib import Path

import yaml

from _phoenix_evidence import application_traces, is_llm_span, model, provider, span_id, span_latency_ms, trace_id, usage

ROOT = Path(__file__).resolve().parents[1]
PHOENIX = ROOT / "traces" / "phoenix_spans.jsonl"
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


spans = load_jsonl(PHOENIX)
if not spans:
    raise RuntimeError("No Phoenix spans available; run scripts/export_traces.py first.")

application = application_traces(spans)
if not application:
    raise RuntimeError("Phoenix export contains no application copilot.run traces.")

provider_config = yaml.safe_load((ROOT / "config/providers.yaml").read_text(encoding="utf-8")) or {}
pricing = {
    name: (
        float(cfg.get("pricing", {}).get("input_usd_per_1m_tokens", 0.0)),
        float(cfg.get("pricing", {}).get("output_usd_per_1m_tokens", 0.0)),
    )
    for name, cfg in provider_config.get("providers", {}).items()
}

# Build the dashboard from the exact same application-trace scope as the golden-signals
# report. Some asynchronous LLM spans are exported under separate trace IDs;
# application_traces() attaches only those LLM spans that execute inside a copilot.run
# window, excluding later DeepEval judge activity.
scoped_spans: list[dict] = []
seen_span_ids: set[str] = set()
for trace in application.values():
    for span in trace["spans"]:
        sid = span_id(span)
        key = sid or f"{span.get('name')}|{span.get('start_time')}|{span.get('end_time')}"
        if key in seen_span_ids:
            continue
        seen_span_ids.add(key)
        scoped_spans.append(span)

rows: list[dict] = []
for span in scoped_spans:
    llm = is_llm_span(span)
    u = usage(span) if llm else {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    p = provider(span) if llm else None
    m = model(span) if llm else None
    estimated_cost = 0.0
    if p in pricing:
        ip, op = pricing[p]
        estimated_cost = u["input_tokens"] / 1_000_000 * ip + u["output_tokens"] / 1_000_000 * op
    rows.append(
        {
            "name": span.get("name"),
            "span_kind": span.get("span_kind") or span.get("attributes.openinference.span.kind"),
            "run_id": span.get("attributes.run_id") or span.get("run_id"),
            "case_id": span.get("attributes.case_id") or span.get("case_id"),
            "trace_id": trace_id(span),
            "span_id": span_id(span),
            "start_time": span.get("start_time"),
            "end_time": span.get("end_time"),
            "latency_ms": round(span_latency_ms(span), 3),
            "provider": p,
            "model": m,
            "provider_status": (str(span.get("status_code")) if llm else None),
            "input_tokens": u["input_tokens"],
            "output_tokens": u["output_tokens"],
            "total_tokens": u["total_tokens"],
            "estimated_cost_usd": round(estimated_cost, 10),
        }
    )

rows.sort(key=lambda r: (r["start_time"] or "", r["trace_id"] or "", r["span_id"] or ""))

csv_path = REPORTS / "dashboard_data.csv"
fields = [
    "name", "span_kind", "run_id", "case_id", "trace_id", "span_id",
    "start_time", "end_time", "latency_ms", "provider", "model", "provider_status",
    "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd",
]
with csv_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

source_sha = hashlib.sha256(PHOENIX.read_bytes()).hexdigest()
(csv_path.with_suffix(csv_path.suffix + ".meta.json")).write_text(
    json.dumps(
        {
            "generated_by": "scripts/build_dashboard.py",
            "source": "traces/phoenix_spans.jsonl",
            "source_sha256": source_sha,
            "application_trace_count": len(application),
            "exported_span_count": len(spans),
            "dashboard_row_count": len(rows),
            "application_scoped_span_count": len(scoped_spans),
            "token_cost_scope": "application copilot.run traces plus LLM spans temporally contained within those runs; DeepEval excluded",
            "latency_scope": "the same application-scoped Phoenix spans",
            "pricing_source": "config/providers.yaml",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

latencies = [float(row["latency_ms"]) for row in rows]
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(10, 5.5))
ax = fig.add_subplot(111)
if latencies:
    ax.plot(range(1, len(latencies) + 1), latencies)
    ax.axhline(statistics.median(latencies), linestyle="--", label="p50")
ax.set_title("Transaction Dispute Copilot — Phoenix-derived Latency Summary")
ax.set_xlabel("Observed Phoenix span")
ax.set_ylabel("Latency (ms)")
ax.grid(True, alpha=0.2)
if latencies:
    ax.legend()
fig.tight_layout()
fig.savefig(REPORTS / "dashboard_summary.png", dpi=160)
plt.close(fig)

print(csv_path)
print(REPORTS / "dashboard_summary.png")
