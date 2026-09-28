from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "traces" / "phoenix_spans.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)

require_phoenix = os.getenv("REQUIRE_PHOENIX_EVIDENCE", "0") == "1"
PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "transaction-dispute-copilot")


def _phoenix_client_base_url() -> str:
    """Real, verified bug: `PHOENIX_ENDPOINT` is used for two contradictory
    things in this project. `src/observability/tracing.py`'s OTLP exporter
    needs the full traces path (`.env.example` sets
    `PHOENIX_ENDPOINT=http://127.0.0.1:6006/v1/traces`), but
    `phoenix.client.Client()`'s own base-URL discovery
    (`phoenix.client.utils.config.get_base_url`, confirmed by reading the
    installed `arize-phoenix-client` source) *also* reads `PHOENIX_ENDPOINT`
    -- treating it as its REST API base URL and appending its own paths
    (e.g. `/v1/spans`) on top. With this project's own `.env` as-is, that
    produces exactly the real, confirmed failure: `Client()` (no explicit
    `base_url`) built a request against
    `http://127.0.0.1:6006/v1/traces/v1/spans` -- the OTLP path with the
    client's own `/v1/spans` suffix appended -- and Phoenix's server
    correctly rejected that malformed URL with `405 Method Not Allowed`,
    because the OTLP traces endpoint at `/v1/traces` only accepts POST for
    ingesting spans, not GET for querying them, and the REST query API lives
    at a completely different path.

    Fixed by deriving the REST client's base URL (the bare server root, no
    path) from the same `PHOENIX_ENDPOINT` value instead of trusting
    `phoenix.client`'s own env-var discovery to do the right thing with a
    value this project already uses for something else, and passing it to
    `Client(base_url=...)` explicitly.
    """
    endpoint = os.getenv("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
    for suffix in ("/v1/traces/", "/v1/traces"):
        if endpoint.endswith(suffix):
            return endpoint[: -len(suffix)] or "http://127.0.0.1:6006"
    # Already a bare base URL (no OTLP path suffix) -- use as-is.
    return endpoint.rstrip("/") or "http://127.0.0.1:6006"


try:
    # `phoenix.Client()` / `client.get_spans_dataframe()` was the API of the
    # full `arize-phoenix` package's older (~4.x-8.x) releases. The current
    # package (verified against a real install: `arize-phoenix==20.16.0`) no
    # longer exposes a top-level `Client` at all -- `import phoenix` still
    # succeeds (it resolves to an empty PEP 420 namespace package when only
    # the lightweight `arize-phoenix-otel` is installed, which is what this
    # project's own venv carries), but `phoenix.Client` raises
    # `AttributeError` either way. The querying client now lives in the
    # separate `arize-phoenix-client` package (a dependency of the full
    # `arize-phoenix`) as `phoenix.client.Client`, and the dataframe method
    # moved to its `.spans` resource. Confirmed for real against a live
    # `phoenix serve` process, not assumed from the changelog alone.
    from phoenix.client import Client

    client = Client(base_url=_phoenix_client_base_url())
    df = client.spans.get_spans_dataframe(project_identifier=PROJECT_NAME)
    records = json.loads(df.to_json(orient="records", date_format="iso"))
    OUT.write_text(
        "\n".join(json.dumps(record, default=str) for record in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    print(f"exported {len(records)} Phoenix spans to {OUT}")
except Exception as exc:
    if require_phoenix:
        raise RuntimeError(
            f"Phoenix evidence is required but Phoenix export failed: {exc}"
        ) from exc

    fallback = ROOT / "logs" / "runtime_spans.jsonl"
    if not fallback.exists():
        raise
    OUT.write_text(fallback.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Phoenix export unavailable ({exc}); copied local diagnostics to {OUT}")
