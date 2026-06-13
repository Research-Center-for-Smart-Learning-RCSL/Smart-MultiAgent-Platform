"""B.5 — /readyz returns problem+json 503 when a dep fails."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shared_kernel.infra.probes.base import ProbeResult


@pytest.fixture
def fake_probes_all_ok(client: TestClient) -> Iterator[TestClient]:
    results = [ProbeResult(n, True) for n in ("postgres", "redis", "qdrant", "neo4j", "minio", "vault")]
    import app.api.v1.readyz as readyz_module

    # Bust the tiny per-process cache so the patch is observed.
    readyz_module._cached = None
    with patch("app.api.v1.readyz.probe_all", new=lambda _s: _coro(results)):
        yield client
    readyz_module._cached = None


@pytest.fixture
def fake_probes_one_down(client: TestClient) -> Iterator[TestClient]:
    results = [
        ProbeResult("postgres", True),
        ProbeResult("redis", False, "connection refused"),
        ProbeResult("qdrant", True),
        ProbeResult("neo4j", True),
        ProbeResult("minio", True),
        ProbeResult("vault", True),
    ]
    import app.api.v1.readyz as readyz_module

    readyz_module._cached = None
    with patch("app.api.v1.readyz.probe_all", new=lambda _s: _coro(results)):
        yield client
    readyz_module._cached = None


async def _coro(value):  # helper — patch target is an async callable
    return value


def test_readyz_green(fake_probes_all_ok: TestClient) -> None:
    r = fake_probes_all_ok.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"postgres", "redis", "qdrant", "neo4j", "minio", "vault"}


def test_readyz_problem_json_on_failure(fake_probes_one_down: TestClient) -> None:
    r = fake_probes_one_down.get("/readyz")
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "https://smap.local/problems/dependency-unavailable"
    assert body["status"] == 503
    assert "redis" in body["detail"]
    assert body["dependencies"]["redis"].startswith("down:")
    assert body["dependencies"]["postgres"] == "ok"


def test_readyz_caches_results_within_window(client: TestClient) -> None:
    """Two consecutive /readyz hits within 2 s share one fan-out (O2.02)."""
    import app.api.v1.readyz as readyz_module
    from shared_kernel.infra.probes.base import ProbeResult

    readyz_module._cached = None
    call_count = {"n": 0}

    async def _counting_probe_all(_settings):
        call_count["n"] += 1
        return [
            ProbeResult(n, True)
            for n in (
                "postgres",
                "redis",
                "qdrant",
                "neo4j",
                "minio",
                "vault",
            )
        ]

    with patch("app.api.v1.readyz.probe_all", new=_counting_probe_all):
        client.get("/readyz")
        client.get("/readyz")
        client.get("/readyz")

    readyz_module._cached = None
    # Three HTTP requests, but probe_all is fanned out only on the first one.
    assert call_count["n"] == 1


def test_readyz_logs_failed_dependencies(
    fake_probes_one_down: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """2.22: /readyz failures should be logged so operators see them
    without having to curl /readyz themselves."""
    import logging

    # loguru → caplog bridge (loguru does not ship with caplog by default).
    from loguru import logger

    handler_id = logger.add(caplog.handler, format="{message}", level="WARNING")
    try:
        with caplog.at_level(logging.WARNING):
            r = fake_probes_one_down.get("/readyz")
        assert r.status_code == 503
        assert any("redis" in record.message and "503" in record.message for record in caplog.records)
    finally:
        logger.remove(handler_id)
