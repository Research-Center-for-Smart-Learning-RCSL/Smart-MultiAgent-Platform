"""B.10 — /metrics is served and returns the seeded counters in Prometheus format."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_exposition_format(client: TestClient) -> None:
    # Produce some traffic first so http_requests_total exists.
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "# HELP http_requests_total" in body
    assert "db_pool_in_use" in body
    assert "redis_command_errors_total" in body
