from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from opentelemetry.trace import Status, StatusCode


logger = logging.getLogger(__name__)


class TraceManager:
    """Phoenix/OpenTelemetry tracing with a machine-readable local evidence stream."""

    def __init__(
        self,
        enabled: bool = True,
        endpoint: str = "http://127.0.0.1:6006/v1/traces",
        log_path: str = "logs/runtime_spans.jsonl",
    ) -> None:
        self.enabled = enabled
        self.endpoint = endpoint
        log_candidate = Path(log_path)
        self.log_path = (Path(__file__).resolve().parents[2] / log_candidate) if not log_candidate.is_absolute() else log_candidate
        self.tracer = None
        self.provider = None
        self.degraded_reason: str | None = None
        self.langchain_instrumented = False
        self._setup()

    def _setup(self) -> None:
        if not self.enabled:
            return

        # Preferred path: Phoenix's OTel registration helper.
        try:
            from phoenix.otel import register
            import phoenix.otel.otel as _phoenix_otel_module

            # Work around a real, verified upstream incompatibility: the pinned
            # arize-phoenix-otel==0.17.1's register() unconditionally calls
            # TracerProvider._tracing_details() as its very last step (even with
            # verbose=False, which only suppresses *printing* that result), and
            # that method reads a private `exporter._headers` attribute that
            # opentelemetry-exporter-otlp-proto-http>=1.45 (this project's own
            # pin) no longer exposes under that name -- raising AttributeError
            # *after* the TracerProvider, exporter and span processor were
            # already fully built and wired up correctly. Without this patch,
            # that AttributeError is silently swallowed by the bare
            # `except Exception: pass` below on every single run, so this
            # "preferred path" never actually succeeds -- confirmed against a
            # real, live `phoenix serve` process (not assumed): register()
            # crashes identically whether or not Phoenix is reachable, so the
            # app always silently fell back to the generic OTLP path further
            # down below, even when a real Phoenix server was up and the
            # export would otherwise have worked. Patching just this one
            # detail-printing method (a no-op under verbose=False anyway)
            # leaves span export, resource attributes and project association
            # untouched.
            _phoenix_otel_module.TracerProvider._tracing_details = lambda self: ""

            self.provider = register(
                project_name="transaction-dispute-copilot",
                endpoint=self.endpoint,
                verbose=False,
            )
            self.tracer = self.provider.get_tracer("transaction-dispute-copilot")
            self._instrument_langchain()
            return
        except Exception as exc:
            self.degraded_reason = type(exc).__name__
            if os.getenv("REQUIRE_PHOENIX_EVIDENCE", "0") == "1":
                raise RuntimeError("PHOENIX_INSTRUMENTATION_UNAVAILABLE") from exc
            logger.warning("Phoenix instrumentation unavailable; using generic OTLP mode: %s", self.degraded_reason)

        # Generic OTLP fallback is an explicitly degraded application mode; strict evidence mode never accepts it.
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider()
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint))
            )
            trace.set_tracer_provider(provider)
            self.provider = provider
            self.tracer = trace.get_tracer("transaction-dispute-copilot")
            self._instrument_langchain()
        except Exception as exc:
            self.degraded_reason = self.degraded_reason or type(exc).__name__
            if os.getenv("REQUIRE_PHOENIX_EVIDENCE", "0") == "1":
                raise RuntimeError("PHOENIX_EXPORTER_UNAVAILABLE") from exc
            self.tracer = None

    def _instrument_langchain(self) -> None:
        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            LangChainInstrumentor().instrument(tracer_provider=self.provider)
            self.langchain_instrumented = True
        except Exception as exc:
            self.langchain_instrumented = False
            self.degraded_reason = self.degraded_reason or type(exc).__name__
            if os.getenv("REQUIRE_PHOENIX_EVIDENCE", "0") == "1":
                raise RuntimeError("LANGCHAIN_TRACING_UNAVAILABLE") from exc
            logger.warning("LangChain tracing unavailable; manual Phoenix spans remain active: %s", type(exc).__name__)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Any]:
        started = time.perf_counter()

        if self.tracer is not None:
            with self.tracer.start_as_current_span(name) as span:
                for key, value in attrs.items():
                    span.set_attribute(key, str(value))
                try:
                    yield span
                except Exception as exc:
                    try:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                        span.set_attribute("error.type", type(exc).__name__)
                    except Exception:
                        logger.debug("Unable to annotate failed span %s", name, exc_info=True)
                    raise
                finally:
                    self._record_local_span(name, attrs, started, span)
        else:
            try:
                yield None
            finally:
                self._record_local_span(name, attrs, started, None)

    def _record_local_span(
        self,
        name: str,
        attrs: dict[str, Any],
        started: float,
        span: Any,
    ) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        trace_id = None
        span_id = None
        if span is not None:
            try:
                context = span.get_span_context()
                trace_id = format(context.trace_id, "032x")
                span_id = format(context.span_id, "016x")
            except Exception:
                pass

        row = {
            "timestamp": time.time(),
            "name": name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "trace_id": trace_id,
            "span_id": span_id,
            **attrs,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")

    @property
    def phoenix_available(self) -> bool:
        return (
            self.provider is not None
            and self.tracer is not None
            and self.langchain_instrumented
            and self.degraded_reason is None
        )


def configure_phoenix(
    enabled: bool = True,
    endpoint: str = "http://127.0.0.1:6006/v1/traces",
) -> TraceManager:
    return TraceManager(enabled=enabled, endpoint=endpoint)
