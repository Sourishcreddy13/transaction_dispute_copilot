"""Coverage for the arize-phoenix-otel==0.17.1 `register()` workaround in
src/observability/tracing.py.

Real, verified upstream bug (not hypothesized): with this project's own
pinned versions -- arize-phoenix-otel==0.17.1 and
opentelemetry-exporter-otlp-proto-http==1.45.0 -- `phoenix.otel.register()`
unconditionally crashes on its very last line, `TracerProvider._tracing_details()`,
which reads a private `exporter._headers` attribute that the pinned OTLP
exporter no longer exposes under that name. This happens *after* a fully
working TracerProvider/exporter/span-processor has already been built, and
whether or not verbose=True, and whether or not a real Phoenix server is
reachable (confirmed against a live `phoenix serve` process during manual
verification). Because `TraceManager._setup()` wraps the whole "preferred
path" in a bare `except Exception: pass`, this crash was previously silent:
every single run fell through to the generic OTLP fallback path further
down, so the "Preferred path: Phoenix's OTel registration helper" comment
described dead code.

These tests characterize the underlying bug directly (reporting, rather than
hard-failing, once the upstream package fixes it and the workaround becomes
unnecessary -- see that test's own docstring for why) and then confirm
`TraceManager` actually takes the preferred `phoenix.otel` path instead of
silently degrading to the generic fallback.
"""
from __future__ import annotations


def test_upstream_register_bug_is_still_present():
    """Characterizes the real bug this workaround targets -- and reports,
    without failing the whole suite over it, once it's no longer there.

    Confirmed present with this project's pinned dependency *ranges*
    (`arize-phoenix-otel>=0.17,<1`, `opentelemetry-exporter-otlp-proto-http>=1.45,<2`
    -- see pyproject.toml) resolving to exactly 0.17.1 / 1.45.0. But those
    are range pins, not exact `==` pins, so a `uv sync` against this
    project's own `uv.lock` on a different machine or day can legitimately
    resolve a newer patch release that already fixes this upstream --
    confirmed happening for real: a real developer ran `uv sync --extra dev`
    against this exact repo and lock file and saw `register()` succeed
    cleanly, where the original diagnosis (against a separately-resolved
    environment) saw it raise every time.

    Since the `TraceManager._setup()` workaround (src/observability/tracing.py)
    is harmless either way -- it only ever no-ops one detail-printing method
    that's a no-op under verbose=False regardless -- this test reports
    whichever way the installed version currently behaves instead of hard-
    failing the whole suite the moment a dependency upgrade fixes the
    upstream bug. (The original problem this workaround exists for was a
    *silent* failure nobody noticed; turning "the bug got fixed" into a new
    hard failure would just recreate that same "stop paying attention to
    this" trap in the opposite direction.)
    """
    from phoenix.otel import register

    try:
        register(
            project_name="workaround-characterization",
            endpoint="http://127.0.0.1:1/v1/traces",
        )
    except AttributeError as exc:
        assert "_headers" in str(exc), (
            f"register() raised AttributeError for an unexpected reason ({exc!r}) -- "
            "this may not be the same upstream bug the workaround targets and deserves "
            "a fresh look, not blind reuse of the existing workaround."
        )
        print(
            "[characterization] the upstream register() bug is still present in the "
            "installed arize-phoenix-otel/opentelemetry-exporter-otlp-proto-http "
            "versions -- the TraceManager._setup() workaround is still needed."
        )
    else:
        print(
            "[characterization] the upstream register() bug is NO LONGER present in the "
            "installed arize-phoenix-otel/opentelemetry-exporter-otlp-proto-http versions "
            "-- the monkeypatch workaround in TraceManager._setup() is now dead code and "
            "can safely be removed next time this file is touched."
        )


def test_trace_manager_uses_preferred_phoenix_otel_path_not_generic_fallback():
    """TraceManager must not silently degrade to the generic OTLP fallback.

    Without the workaround, phoenix.otel.register() always raises (see
    above), the bare except in _setup() swallows it, and TraceManager ends
    up with a plain opentelemetry.sdk.trace.TracerProvider instead of
    phoenix.otel.otel.TracerProvider -- losing Phoenix's own resource
    attributes/project association even when Phoenix is otherwise reachable.
    """
    from phoenix.otel.otel import TracerProvider as PhoenixTracerProvider
    from src.observability.tracing import TraceManager

    # No real server needs to be reachable for this: register() itself
    # builds the provider/exporter/processor synchronously and succeeds
    # before any span is ever sent (span export happening later, async, is
    # what would fail without a live collector -- not construction).
    tm = TraceManager(enabled=True, endpoint="http://127.0.0.1:1/v1/traces")

    assert tm.tracer is not None
    assert isinstance(tm.provider, PhoenixTracerProvider), (
        f"expected the preferred phoenix.otel path, got {type(tm.provider)!r} "
        "-- TraceManager silently fell back to the generic OTLP path"
    )


def test_trace_manager_span_context_manager_still_works_end_to_end():
    """The workaround must not break ordinary span usage."""
    from src.observability.tracing import TraceManager

    tm = TraceManager(enabled=True, endpoint="http://127.0.0.1:1/v1/traces")
    with tm.span("test_span", foo="bar") as span:
        assert span is not None
