from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "logs" / "runtime_spans.jsonl"
PROVIDER = ROOT / "logs" / "model_provider.jsonl"
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


runtime = load(RUNTIME)
providers = load(PROVIDER)
provider_by_run: dict[str, dict] = {}
for row in providers:
    run_id = row.get("run_id")
    if run_id:
        provider_by_run[run_id] = row

rows: list[dict] = []
for row in runtime:
    run_id = row.get("run_id")
    usage = row.get("usage") or {}
    provider = provider_by_run.get(run_id, {})
    rows.append(
        {
            "name": row.get("name"),
            "run_id": run_id,
            "case_id": row.get("case_id"),
            "trace_id": row.get("trace_id"),
            "span_id": row.get("span_id"),
            "latency_ms": row.get("latency_ms", 0),
            "provider": provider.get("provider"),
            "provider_status": provider.get("status"),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "estimated_cost_usd": row.get("estimated_cost_usd", 0),
        }
    )

csv_path = REPORTS / "dashboard_data.csv"
fields = list(rows[0].keys()) if rows else [
    "name", "run_id", "case_id", "trace_id", "span_id", "latency_ms",
    "provider", "provider_status", "input_tokens", "output_tokens",
    "total_tokens", "estimated_cost_usd",
]
with csv_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
fig = plt.figure(figsize=(10, 5.5))
ax = fig.add_subplot(111)
if latencies:
    ax.plot(range(1, len(latencies) + 1), latencies, marker="o")
    ax.axhline(statistics.median(latencies), linestyle="--", label="p50")
ax.set_title("Transaction Dispute Copilot — Latency / Token Evidence")
ax.set_xlabel("Observed execution span")
ax.set_ylabel("Latency (ms)")
ax.grid(True, alpha=0.2)
if latencies:
    ax.legend()
fig.tight_layout()
fig.savefig(REPORTS / "dashboard.png", dpi=160)
plt.close(fig)

print(csv_path)
print(REPORTS / "dashboard.png")
