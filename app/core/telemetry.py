from __future__ import annotations

from src.observability.tracing import TraceManager


class Telemetry(TraceManager):
    """Backward-compatible alias for the canonical Phoenix TraceManager.

    The old implementation silently disabled telemetry on any initialization
    error. Keeping a thin alias prevents callers from accidentally reintroducing
    that unsafe behavior while preserving the legacy constructor name.
    """

    def __init__(self, enabled: bool = True, phoenix_endpoint: str = "") -> None:
        super().__init__(
            enabled=enabled,
            endpoint=phoenix_endpoint or "http://127.0.0.1:6006/v1/traces",
        )


TelemetryManager = Telemetry
