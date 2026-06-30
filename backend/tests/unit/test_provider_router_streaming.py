"""K.1 — ProviderRouter.call_stream rotation semantics + call_single_key accounting.

Streaming contract (§K Risks): a member failing *before* the first token
rotates to the next key; a failure *after* the first token raises
``ProviderStreamError`` (partial output cannot be replayed across keys). The
single-key path records a usage row without rotating (embed/rerank pin a key).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

import contexts.keys.application.provider_router as pr
from contexts.keys.application.provider_router import (
    ProviderCallResult,
    ProviderRequest,
    ProviderRouter,
    ProviderStreamError,
    RouterConfig,
    StreamComplete,
    TokenDelta,
)
from contexts.keys.domain.errors import CapabilityMismatch, KeyGroupExhausted
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability

_CHAT = ProviderRequest(
    capability=ProviderCapability.LLM_CHAT,
    payload={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
)


class _Rot:
    rotate_on_error_codes: frozenset[int] = frozenset()
    retry_on_error = False


class _Member:
    def __init__(self, key_id: uuid.UUID) -> None:
        self.key_id = key_id
        self.rotation = _Rot()


class _Key:
    def __init__(self, key_id: uuid.UUID, provider: ApiKeyProvider) -> None:
        self.id = key_id
        self.provider = provider


class _MembersRepo:
    def __init__(self, members: list[_Member]) -> None:
        self._m = members

    async def list_ordered_carried(self, _gid: uuid.UUID) -> list[_Member]:
        return self._m


class _KeysRepo:
    def __init__(self, keys: dict[uuid.UUID, _Key]) -> None:
        self._k = keys

    async def get_active(self, kid: uuid.UUID) -> _Key | None:
        return self._k.get(kid)


class _StreamAdapter:
    """Yields a scripted event list; an Exception entry is raised in-stream."""

    def __init__(self, provider: ApiKeyProvider, events: list) -> None:
        self.provider = provider
        self._events = events

    async def stream(self, *, secret: str, request: ProviderRequest):
        for ev in self._events:
            if isinstance(ev, BaseException):
                raise ev
            yield ev

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        return self._events[0].result  # only used by single-key tests


def _make_router(monkeypatch, adapters, members, keys) -> tuple[ProviderRouter, list[dict]]:
    recorded: list[dict] = []

    async def _rec(_db, **kw):
        recorded.append(kw)

    async def _bucket(*_a, **_k):
        return None

    monkeypatch.setattr(pr, "record_usage_event", _rec)
    monkeypatch.setattr(pr.redis_buckets, "record", _bucket)

    from contexts.keys.application.provider_router import UsageAccountant

    r = ProviderRouter.__new__(ProviderRouter)
    r._db = object()  # type: ignore[attr-defined]
    r._adapters = adapters  # type: ignore[attr-defined]
    r._members_repo = _MembersRepo(members)  # type: ignore[attr-defined]
    r._keys_repo = _KeysRepo(keys)  # type: ignore[attr-defined]
    r._config = RouterConfig()  # type: ignore[attr-defined]
    r._accountant = UsageAccountant(r._db)  # type: ignore[attr-defined]

    async def _no_quota(_em):
        return False

    async def _secret(_kid):
        return b"secret-bytes"

    r._quota_exceeded = _no_quota  # type: ignore[attr-defined,method-assign]
    r._unwrap_secret = _secret  # type: ignore[attr-defined,method-assign]
    return r, recorded


async def _drain(agen) -> list:
    return [e async for e in agen]


@pytest.mark.asyncio
async def test_stream_success_records_usage(monkeypatch) -> None:
    kid = uuid.uuid4()
    ok = _StreamAdapter(
        ApiKeyProvider.OPENAI,
        [
            TokenDelta("Hel"),
            TokenDelta("lo"),
            StreamComplete(ProviderCallResult(200, {"text": "Hello"}, 5, 2)),
        ],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: ok},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    events = await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["Hel", "lo"]
    assert isinstance(events[-1], StreamComplete)
    assert len(recorded) == 1
    assert recorded[0]["output_tokens"] == 2
    assert recorded[0]["http_status"] == 200


@pytest.mark.asyncio
async def test_stream_rotates_before_first_token(monkeypatch) -> None:
    k1, k2 = uuid.uuid4(), uuid.uuid4()
    fail = _StreamAdapter(ApiKeyProvider.OPENAI, [StreamComplete(ProviderCallResult(500, {"error": "x"}))])
    ok = _StreamAdapter(
        ApiKeyProvider.CLAUDE,
        [TokenDelta("ok"), StreamComplete(ProviderCallResult(200, {"text": "ok"}, 1, 1))],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: fail, ApiKeyProvider.CLAUDE: ok},
        [_Member(k1), _Member(k2)],
        {k1: _Key(k1, ApiKeyProvider.OPENAI), k2: _Key(k2, ApiKeyProvider.CLAUDE)},
    )
    events = await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["ok"]
    # Both the failed (500) and the succeeding (200) call were accounted.
    assert {r["http_status"] for r in recorded} == {500, 200}


@pytest.mark.asyncio
async def test_stream_fails_after_first_token_raises(monkeypatch) -> None:
    kid = uuid.uuid4()
    bad = _StreamAdapter(
        ApiKeyProvider.OPENAI,
        [TokenDelta("par"), StreamComplete(ProviderCallResult(500, {"error": "x"}))],
    )
    router, _ = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: bad},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    seen: list[str] = []

    async def _consume() -> None:
        async for e in router.call_stream(group_id=uuid.uuid4(), request=_CHAT):
            if isinstance(e, TokenDelta):
                seen.append(e.text)

    with pytest.raises(ProviderStreamError):
        await _consume()
    assert seen == ["par"]  # the partial token reached the consumer before the failure


@pytest.mark.asyncio
async def test_stream_all_fail_raises_exhausted(monkeypatch) -> None:
    kid = uuid.uuid4()
    fail = _StreamAdapter(ApiKeyProvider.OPENAI, [StreamComplete(ProviderCallResult(500, {"error": "x"}))])
    router, _ = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: fail},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    with pytest.raises(KeyGroupExhausted):
        await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))


@pytest.mark.asyncio
async def test_stream_aborts_group_on_request_rejection_without_rotating(monkeypatch) -> None:
    # A deterministic 400 (bad request) must NOT rotate to the sibling key — it
    # would 400 identically and burn the group. Abort after the first member.
    k1, k2 = uuid.uuid4(), uuid.uuid4()
    bad = _StreamAdapter(
        ApiKeyProvider.OPENAI, [StreamComplete(ProviderCallResult(400, {"error": "HTTP 400"}))]
    )
    sibling = _StreamAdapter(
        ApiKeyProvider.CLAUDE,
        [TokenDelta("ok"), StreamComplete(ProviderCallResult(200, {"text": "ok"}, 1, 1))],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: bad, ApiKeyProvider.CLAUDE: sibling},
        [_Member(k1), _Member(k2)],
        {k1: _Key(k1, ApiKeyProvider.OPENAI), k2: _Key(k2, ApiKeyProvider.CLAUDE)},
    )
    with pytest.raises(KeyGroupExhausted) as ei:
        await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))
    assert ei.value.reason == "request_rejected"
    # Only the first (400) member was tried + accounted; the sibling never ran.
    assert {r["http_status"] for r in recorded} == {400}


@pytest.mark.asyncio
async def test_stream_consumer_abort_records_client_abort(monkeypatch) -> None:
    # Consumer walks away after the first token: accounting must still fire
    # (via the aclose chain) and label it `client_abort`, NOT `transport_error`.
    kid = uuid.uuid4()
    adapter = _StreamAdapter(
        ApiKeyProvider.OPENAI,
        [
            TokenDelta("a"),
            TokenDelta("b"),
            StreamComplete(ProviderCallResult(200, {"text": "ab"}, 1, 2)),
        ],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: adapter},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    agen = router.call_stream(group_id=uuid.uuid4(), request=_CHAT)
    got: list = []
    async for e in agen:
        got.append(e)
        break  # abandon mid-stream
    await agen.aclose()
    assert [e.text for e in got if isinstance(e, TokenDelta)] == ["a"]
    assert len(recorded) == 1
    assert recorded[0]["error_code"] == "client_abort"
    assert recorded[0]["http_status"] is None


@pytest.mark.asyncio
async def test_stream_transport_error_before_first_token_rotates(monkeypatch) -> None:
    k1, k2 = uuid.uuid4(), uuid.uuid4()
    boom = _StreamAdapter(ApiKeyProvider.OPENAI, [httpx.ConnectError("boom")])
    ok = _StreamAdapter(
        ApiKeyProvider.CLAUDE,
        [TokenDelta("ok"), StreamComplete(ProviderCallResult(200, {"text": "ok"}, 1, 1))],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: boom, ApiKeyProvider.CLAUDE: ok},
        [_Member(k1), _Member(k2)],
        {k1: _Key(k1, ApiKeyProvider.OPENAI), k2: _Key(k2, ApiKeyProvider.CLAUDE)},
    )
    events = await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["ok"]
    codes = [r["error_code"] for r in recorded]
    assert "transport_error" in codes  # the failed member was accounted


@pytest.mark.asyncio
async def test_stream_transport_error_after_first_token_raises(monkeypatch) -> None:
    kid = uuid.uuid4()
    adapter = _StreamAdapter(ApiKeyProvider.OPENAI, [TokenDelta("par"), httpx.ConnectError("mid-stream")])
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: adapter},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    seen: list[str] = []

    async def _consume() -> None:
        async for e in router.call_stream(group_id=uuid.uuid4(), request=_CHAT):
            if isinstance(e, TokenDelta):
                seen.append(e.text)

    # After the first token a transport failure is NOT retried across keys —
    # it surfaces to the caller (R9.09) and is accounted as transport_error.
    with pytest.raises(httpx.ConnectError):
        await _consume()
    assert seen == ["par"]
    assert recorded[0]["error_code"] == "transport_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [529, 429])  # synthetic in-stream error statuses
async def test_stream_rotates_on_synthetic_in_stream_error_status(monkeypatch, status) -> None:
    # Adapters map mid-stream provider error events (delivered inside an HTTP
    # 200) onto synthetic non-2xx StreamCompletes — before the first token the
    # router must rotate, exactly as for a real non-2xx response.
    k1, k2 = uuid.uuid4(), uuid.uuid4()
    fail = _StreamAdapter(
        ApiKeyProvider.OPENAI,
        [StreamComplete(ProviderCallResult(status, {"error": f"HTTP {status} (x)"}))],
    )
    ok = _StreamAdapter(
        ApiKeyProvider.CLAUDE,
        [TokenDelta("ok"), StreamComplete(ProviderCallResult(200, {"text": "ok"}, 1, 1))],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: fail, ApiKeyProvider.CLAUDE: ok},
        [_Member(k1), _Member(k2)],
        {k1: _Key(k1, ApiKeyProvider.OPENAI), k2: _Key(k2, ApiKeyProvider.CLAUDE)},
    )
    events = await _drain(router.call_stream(group_id=uuid.uuid4(), request=_CHAT))
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["ok"]
    assert {r["http_status"] for r in recorded} == {status, 200}


@pytest.mark.asyncio
async def test_stream_propagates_attribution_into_usage(monkeypatch) -> None:
    # agent_id / parent_agent_id / chatroom_id ride the request into every
    # recorded usage event (R7.12 attribution).
    kid = uuid.uuid4()
    aid, pid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        agent_id=aid,
        parent_agent_id=pid,
        chatroom_id=cid,
    )
    adapter = _StreamAdapter(
        ApiKeyProvider.OPENAI,
        [TokenDelta("ok"), StreamComplete(ProviderCallResult(200, {"text": "ok"}, 1, 1))],
    )
    router, recorded = _make_router(
        monkeypatch,
        {ApiKeyProvider.OPENAI: adapter},
        [_Member(kid)],
        {kid: _Key(kid, ApiKeyProvider.OPENAI)},
    )
    await _drain(router.call_stream(group_id=uuid.uuid4(), request=req))
    assert len(recorded) == 1
    assert recorded[0]["agent_id"] == aid
    assert recorded[0]["parent_agent_id"] == pid
    assert recorded[0]["chatroom_id"] == cid


@pytest.mark.asyncio
async def test_single_key_records_and_returns(monkeypatch) -> None:
    kid = uuid.uuid4()
    embed_req = ProviderRequest(
        capability=ProviderCapability.EMBEDDING,
        payload={"model": "voyage-3", "input": ["x"]},
    )
    adapter = _StreamAdapter(
        ApiKeyProvider.VOYAGE,
        [StreamComplete(ProviderCallResult(200, {"embeddings": [[0.1]]}, 4))],
    )
    router, recorded = _make_router(
        monkeypatch, {ApiKeyProvider.VOYAGE: adapter}, [], {kid: _Key(kid, ApiKeyProvider.VOYAGE)}
    )
    res = await router.call_single_key(key_id=kid, request=embed_req)
    assert res.body["embeddings"] == [[0.1]]
    assert len(recorded) == 1
    assert recorded[0]["key_id"] == kid
    assert recorded[0]["error_code"] is None


@pytest.mark.asyncio
async def test_single_key_capability_mismatch(monkeypatch) -> None:
    kid = uuid.uuid4()
    router, _ = _make_router(
        monkeypatch,
        {ApiKeyProvider.VOYAGE: _StreamAdapter(ApiKeyProvider.VOYAGE, [])},
        [],
        {kid: _Key(kid, ApiKeyProvider.VOYAGE)},
    )
    # VOYAGE serves EMBEDDING only — an LLM_CHAT request must be refused.
    with pytest.raises(CapabilityMismatch):
        await router.call_single_key(key_id=kid, request=_CHAT)
