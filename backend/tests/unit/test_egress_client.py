"""SEC-H5 — the concrete EgressProxyClient signs the HMAC and routes through
the proxy (it does not call providers directly).

Asserts the three headers the proxy authenticates on are present and correct,
that an upstream success round-trips, that a proxy *denial* (problem+json with
a ``mcp-egress-denied`` type) raises :class:`McpEgressDenied`, and that a
missing shared secret fails closed without any network call.
"""

from __future__ import annotations

import hmac
import json
import uuid
from hashlib import sha256

import httpx
import pytest
import respx

from contexts.agents.domain.errors import McpEgressDenied
from contexts.agents.infrastructure.egress_client import HttpxEgressProxyClient

_PROXY = "http://egress-proxy:8080"
_SECRET = bytes.fromhex("00" * 31 + "01")
_TARGET = "https://api.tavily.com/search"


def _client() -> HttpxEgressProxyClient:
    return HttpxEgressProxyClient(proxy_base_url=_PROXY, shared_secret=_SECRET)


@respx.mock
async def test_signs_hmac_and_sets_forward_url() -> None:
    route = respx.post(_PROXY).mock(return_value=httpx.Response(200, json={"results": []}))
    pid = uuid.uuid4()

    status, _headers, body = await _client().request(
        method="POST",
        url=_TARGET,
        project_id=pid,
        headers={"content-type": "application/json"},
        json_body={"query": "hi"},
    )

    assert status == 200
    assert json.loads(body) == {"results": []}
    sent = route.calls.last.request
    assert sent.headers["x-smap-egress-url"] == _TARGET
    assert sent.headers["x-smap-project-id"] == str(pid)
    expected = hmac.new(_SECRET, str(pid).encode("ascii"), sha256).hexdigest()
    assert sent.headers["x-smap-egress-hmac"] == expected


@respx.mock
async def test_strips_caller_authorization_header() -> None:
    route = respx.post(_PROXY).mock(return_value=httpx.Response(200, json={}))
    await _client().request(
        method="POST",
        url=_TARGET,
        project_id=uuid.uuid4(),
        headers={"authorization": "Bearer platform-secret", "cookie": "sid=abc"},
        json_body={},
    )
    sent = route.calls.last.request
    assert "authorization" not in {k.lower() for k in sent.headers}
    assert "cookie" not in {k.lower() for k in sent.headers}


@respx.mock
async def test_proxy_denial_raises_egress_denied() -> None:
    respx.post(_PROXY).mock(
        return_value=httpx.Response(
            403,
            headers={"content-type": "application/problem+json"},
            content=json.dumps(
                {"type": "urn:smap:error:mcp-egress-denied", "detail": "host not on allowlist"}
            ).encode(),
        )
    )
    with pytest.raises(McpEgressDenied):
        await _client().request(method="POST", url=_TARGET, project_id=uuid.uuid4(), json_body={})


@respx.mock
async def test_upstream_4xx_is_passed_through_not_denied() -> None:
    # A 4xx from the *real target* (not a proxy problem+json) must round-trip
    # so the caller can react — it is not a proxy denial.
    respx.post(_PROXY).mock(return_value=httpx.Response(404, json={"error": "not found"}))
    status, _headers, _body = await _client().request(
        method="POST", url=_TARGET, project_id=uuid.uuid4(), json_body={}
    )
    assert status == 404


async def test_missing_secret_fails_closed() -> None:
    client = HttpxEgressProxyClient(proxy_base_url=_PROXY, shared_secret=b"")
    with pytest.raises(McpEgressDenied):
        await client.request(method="POST", url=_TARGET, project_id=uuid.uuid4(), json_body={})
