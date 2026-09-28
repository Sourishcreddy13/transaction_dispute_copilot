from __future__ import annotations
import json, logging, time
from contextlib import contextmanager
logger=logging.getLogger('dispute_copilot')
logging.basicConfig(level=logging.INFO,format='%(message)s')

class Telemetry:
    def __init__(self, enabled=True, phoenix_endpoint=''):
        self.tracer=None
        if enabled:
            try:
                from opentelemetry import trace
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                provider=TracerProvider();
                if phoenix_endpoint:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint)))
                trace.set_tracer_provider(provider); self.tracer=trace.get_tracer('transaction-dispute-copilot')
            except Exception:
                self.tracer=None
    @contextmanager
    def span(self,name,**attrs):
        if self.tracer:
            with self.tracer.start_as_current_span(name) as s:
                for k,v in attrs.items(): s.set_attribute(k,str(v))
                yield s
        else:
            start=time.perf_counter(); yield None
            logger.info(json.dumps({'event':'span','name':name,'latency_ms':round((time.perf_counter()-start)*1000,2),**attrs}))
