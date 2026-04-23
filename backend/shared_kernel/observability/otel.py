"""OpenTelemetry wiring — structural only, opt-in.

The entire OTEL SDK is an optional extra (`pip install -e .[otel]`). If the
extra is not installed or `OTEL_EXPORTER_OTLP_ENDPOINT` is not set, this
module is a no-op. Structural per `docs/operations.md` §9.

SoC: we keep OTEL instrumentation attachments here instead of sprinkling them
across modules, so that removing observability is a one-file change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.settings import ObservabilitySection

if TYPE_CHECKING:  # type-only import — keeps FastAPI out of OTEL-disabled paths
    from fastapi import FastAPI


def install_otel(app: "FastAPI", cfg: ObservabilitySection) -> None:
    if not cfg.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # otel extra not installed; structural stub stays silent per §9.
        return

    resource = Resource.create({"service.name": cfg.otel_service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=cfg.otel_exporter_otlp_endpoint)
        )
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument()


__all__ = ["install_otel"]
