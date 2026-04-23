"""Observability primitives (B.10): metrics registry + optional OTEL wiring.

SoC: this package talks to Prometheus and OpenTelemetry SDKs; it does not
import any bounded context. `app.api.v1.metrics` mounts the endpoint;
`app.main` hooks in the middleware and OTEL install once.
"""
