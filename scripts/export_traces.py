from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "traces" / "phoenix_spans.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)
PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "transaction-dispute-copilot")


def _phoenix_client_base_url() -> str:
    endpoint = os.getenv("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
    for suffix in ("/v1/traces/", "/v1/traces"):
        if endpoint.endswith(suffix):
            return endpoint[: -len(suffix)] or "http://127.0.0.1:6006"
    return endpoint.rstrip("/") or "http://127.0.0.1:6006"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


try:
    from phoenix.client import Client

    evidence_start_raw = os.getenv("EVIDENCE_START_UTC")
    if not evidence_start_raw:
        raise RuntimeError(
            "EVIDENCE_START_UTC is required. Run scripts/generate_evidence.py "
            "so Phoenix export is scoped to the current evidence run."
        )
    evidence_start = _parse_time(evidence_start_raw)
    if evidence_start is None:
        raise RuntimeError(f"Invalid EVIDENCE_START_UTC: {evidence_start_raw}")

    client = Client(base_url=_phoenix_client_base_url())
    df = client.spans.get_spans_dataframe(
        project_identifier=PROJECT_NAME,
        start_time=evidence_start,
        limit=10000,
    )
    records = json.loads(df.to_json(orient="records", date_format="iso"))

    # Guard against SDK/server implementations that ignore start_time.
    filtered: list[dict] = []
    for record in records:
        started = _parse_time(record.get("start_time"))
        if started is None or started >= evidence_start:
            filtered.append(record)

    if not filtered:
        raise RuntimeError(
            f"Phoenix returned zero spans for project '{PROJECT_NAME}' "
            f"at/after {evidence_start.isoformat()}."
        )

    end_time = datetime.now(timezone.utc).isoformat()
    OUT.write_text(
        "\n".join(json.dumps(record, default=str) for record in filtered) + "\n",
        encoding="utf-8",
    )
    OUT.with_suffix(OUT.suffix + ".meta.json").write_text(
        json.dumps(
            {
                "generated_by": "scripts/export_traces.py",
                "source": "Arize Phoenix",
                "project": PROJECT_NAME,
                "span_count": len(filtered),
                "evidence_start_utc": evidence_start.isoformat(),
                "evidence_end_utc": end_time,
                "filter": "Phoenix project spans at/after EVIDENCE_START_UTC",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"exported {len(filtered)} Phoenix spans to {OUT}")
except Exception as exc:
    raise RuntimeError(
        f"Phoenix evidence export failed: {exc}. No non-Phoenix fallback is permitted."
    ) from exc
