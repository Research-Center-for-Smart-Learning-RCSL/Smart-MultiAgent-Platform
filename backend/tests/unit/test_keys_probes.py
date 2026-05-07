"""D.3 — each provider probe hits the right endpoint and maps responses correctly."""

from __future__ import annotations

import httpx
import pytest
import respx

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes import PROBES, ProbeStatus, probe


@pytest.mark.asyncio()
@respx.mock
async def test_anthropic_probe_ok() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(200, json={"id": "msg_x"})
    result = await probe(ApiKeyProvider.CLAUDE, "sk-ant-abc")
    assert route.called
    assert result.status is ProbeStatus.OK
    assert route.calls.last.request.headers["x-api-key"] == "sk-ant-abc"


@pytest.mark.asyncio()
@respx.mock
async def test_anthropic_probe_401() -> None:
    respx.post("https://api.anthropic.com/v1/messages").respond(
        401, json={"error": {"type": "authentication_error"}}
    )
    result = await probe(ApiKeyProvider.CLAUDE, "bad")
    assert result.status is ProbeStatus.FAILED
    assert result.error == "unauthorized"


@pytest.mark.asyncio()
@respx.mock
async def test_openai_probe_ok() -> None:
    route = respx.get("https://api.openai.com/v1/models").respond(200, json={"data": []})
    result = await probe(ApiKeyProvider.OPENAI, "sk-openai-live")
    assert route.called
    assert result.status is ProbeStatus.OK
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-openai-live"


@pytest.mark.asyncio()
@respx.mock
async def test_gemini_probe_uses_query_key() -> None:
    route = respx.get("https://generativelanguage.googleapis.com/v1/models").respond(200, json={})
    result = await probe(ApiKeyProvider.GEMINI, "AIzaSecret")
    assert result.status is ProbeStatus.OK
    # Key must be on the query string, not a header.
    assert "key=AIzaSecret" in str(route.calls.last.request.url)
    assert "authorization" not in route.calls.last.request.headers


@pytest.mark.asyncio()
@respx.mock
async def test_voyage_probe_ok() -> None:
    route = respx.post("https://api.voyageai.com/v1/embeddings").respond(
        200, json={"data": [{"embedding": [0.0]}]}
    )
    result = await probe(ApiKeyProvider.VOYAGE, "vy-key")
    assert route.called
    assert result.status is ProbeStatus.OK


@pytest.mark.asyncio()
@respx.mock
async def test_cohere_probe_ok() -> None:
    route = respx.get("https://api.cohere.com/v1/models").respond(200, json={"models": []})
    result = await probe(ApiKeyProvider.COHERE, "co-key")
    assert route.called
    assert result.status is ProbeStatus.OK


@pytest.mark.asyncio()
@respx.mock
async def test_probe_network_error_returns_failed() -> None:
    respx.get("https://api.openai.com/v1/models").mock(side_effect=httpx.ConnectError("boom"))
    result = await probe(ApiKeyProvider.OPENAI, "sk-x")
    assert result.status is ProbeStatus.FAILED
    assert "network" in (result.error or "")


@pytest.mark.asyncio()
@respx.mock
async def test_probe_server_error_summarises_without_secret() -> None:
    respx.get("https://api.openai.com/v1/models").respond(
        500, json={"error": {"type": "server_error", "message": "contains sk-secret-xyz"}}
    )
    result = await probe(ApiKeyProvider.OPENAI, "sk-secret-xyz")
    assert result.status is ProbeStatus.FAILED
    assert "HTTP 500" in (result.error or "")
    assert "sk-secret-xyz" not in (result.error or "")


def test_dispatch_covers_every_provider() -> None:
    # Every enum member must have a probe — missing one is a bug that must
    # not ship. The D.∞ gate explicitly requires cohere rerank coverage.
    for member in ApiKeyProvider:
        assert member in PROBES
