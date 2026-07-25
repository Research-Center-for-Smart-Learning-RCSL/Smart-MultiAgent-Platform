"""Unit tests for POST /api/projects/{pid}/search-keys/{id}/activate (R12.16).

The egress-allowlist seed lives here, not inside SearchKeyService.activate()
(see test_search_key_service.py's docstring for why) -- these tests pin the
route's orchestration: it calls SearchKeyService.activate(), then seeds the
returned provider's one documented hostname via AgentsFacade, sharing the
request's `db` so a failure rolls back the whole request (Q-5).
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

import app.api.v1.search_keys as search_keys_module
import contexts.agents.interfaces.facade as facade_module
from app.api.v1.search_keys import activate_search_key
from contexts.keys.domain.search import SEARCH_PROVIDER_HOSTS, SearchProvider
from shared_kernel.auth.context import RequestContext


class _FakeSearchKeyService:
    """Fakes ``SearchKeyService(db).activate(...) -> SearchProvider``."""

    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    async def activate(self, **_: Any) -> SearchProvider:
        return self._provider


class _RecordingFacade:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, db: Any) -> None:
        self.db = db

    async def add_egress_allowlist_host(self, **kwargs: Any) -> None:
        _RecordingFacade.calls.append(kwargs)


class _RaisingFacade:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def add_egress_allowlist_host(self, **_: Any) -> None:
        raise RuntimeError("allowlist write failed")


def _principal() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(user_id=uuid.uuid4(), is_admin=False)


async def _activate(monkeypatch: pytest.MonkeyPatch, *, provider: SearchProvider, facade: type) -> None:
    monkeypatch.setattr(search_keys_module, "SearchKeyService", lambda _db: _FakeSearchKeyService(provider))
    monkeypatch.setattr(facade_module, "AgentsFacade", facade)

    await activate_search_key(
        project_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        principal=_principal(),
        ctx=RequestContext(actor_ip="203.0.113.9"),
        db=object(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "expected_host"), list(SEARCH_PROVIDER_HOSTS.items()))
async def test_route_seeds_exactly_the_activated_providers_host(
    monkeypatch: pytest.MonkeyPatch, provider: SearchProvider, expected_host: str
) -> None:
    _RecordingFacade.calls.clear()
    await _activate(monkeypatch, provider=provider, facade=_RecordingFacade)

    assert len(_RecordingFacade.calls) == 1
    call = _RecordingFacade.calls[0]
    assert call["hostname"] == expected_host
    other_hosts = set(SEARCH_PROVIDER_HOSTS.values()) - {expected_host}
    assert call["hostname"] not in other_hosts


@pytest.mark.asyncio
async def test_route_attributes_the_seed_to_the_activating_user_and_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingFacade.calls.clear()
    await _activate(monkeypatch, provider=SearchProvider.TAVILY, facade=_RecordingFacade)

    call = _RecordingFacade.calls[0]
    assert call["actor_ip"] == "203.0.113.9"
    assert isinstance(call["actor_user_id"], uuid.UUID)


@pytest.mark.asyncio
async def test_route_propagates_allowlist_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="allowlist write failed"):
        await _activate(monkeypatch, provider=SearchProvider.TAVILY, facade=_RaisingFacade)
