from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

import contexts.agents.interfaces.facade as facade_module
from contexts.keys.application.search_service import SearchKeyService
from contexts.keys.domain.probe_status import ProbeStatus
from contexts.keys.domain.search import SEARCH_PROVIDER_HOSTS, SearchKey, SearchProvider


class _FakeSession:
    pass


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class _RecordingFacade:
    """Fake ``AgentsFacade`` capturing ``add_egress_allowlist_host`` calls.

    Class-level ``calls`` because :meth:`SearchKeyService.activate`
    instantiates the facade itself; the test cannot reach into that instance.
    """

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


def _key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    provider: SearchProvider = SearchProvider.TAVILY,
) -> SearchKey:
    return SearchKey(
        id=key_id,
        project_id=project_id,
        provider=provider,
        masked_preview="****",
        test_status=ProbeStatus.OK,
        test_error=None,
        last_test_at=datetime.now(tz=UTC),
        is_active=False,
        config={},
        transit_key_version=1,
        hmac_key_version=1,
        created_at=datetime.now(tz=UTC),
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_activate_audits_each_deactivated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    project_id = uuid.uuid4()
    replacement_key_id = uuid.uuid4()
    deactivated_key_id = uuid.uuid4()
    events: list[Any] = []

    class _Repo:
        async def get_active(self, key_id: uuid.UUID) -> SearchKey | None:
            return _key(project_id, key_id)

        async def atomic_activate(self, **_: Any) -> list[uuid.UUID]:
            return [deactivated_key_id]

    async def _emit(_db: Any, event: Any) -> None:
        events.append(event)

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo()  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(facade_module, "AgentsFacade", _RecordingFacade)

    await service.activate(
        project_id=project_id,
        key_id=replacement_key_id,
        actor_user_id=uuid.uuid4(),
    )

    assert [(event.action, event.resource_id) for event in events] == [
        ("search_key.deactivated", deactivated_key_id),
        ("search_key.activated", replacement_key_id),
    ]


class _Repo:
    """Minimal ``SearchKeyRepository`` stand-in for a fixed provider's key."""

    def __init__(self, project_id: uuid.UUID, provider: SearchProvider) -> None:
        self._project_id = project_id
        self._provider = provider

    async def get_active(self, key_id: uuid.UUID) -> SearchKey | None:
        return _key(self._project_id, key_id, self._provider)

    async def atomic_activate(self, **_: Any) -> list[uuid.UUID]:
        return []


async def _noop_emit(_db: Any, _event: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_activate_adds_provider_host_to_egress_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    _RecordingFacade.calls.clear()
    project_id = uuid.uuid4()
    key_id = uuid.uuid4()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, SearchProvider.TAVILY)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(facade_module, "AgentsFacade", _RecordingFacade)

    await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    assert len(_RecordingFacade.calls) == 1
    call = _RecordingFacade.calls[0]
    assert call["project_id"] == project_id
    assert call["hostname"] == "api.tavily.com"


@pytest.mark.asyncio
async def test_activate_is_idempotent_when_host_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    _RecordingFacade.calls.clear()
    project_id = uuid.uuid4()
    key_id = uuid.uuid4()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, SearchProvider.TAVILY)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(facade_module, "AgentsFacade", _RecordingFacade)

    await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())
    await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    # No error on repeat activation; the same host is submitted both times.
    # Real duplicate-suppression is the repository's ON CONFLICT DO UPDATE
    # (pinned in test_egress_allowlist.py), which this fake does not model.
    assert [c["hostname"] for c in _RecordingFacade.calls] == ["api.tavily.com", "api.tavily.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "expected_host"), list(SEARCH_PROVIDER_HOSTS.items()))
async def test_activate_does_not_add_other_providers_hosts(
    monkeypatch: pytest.MonkeyPatch, provider: SearchProvider, expected_host: str
) -> None:
    import contexts.keys.application.search_service as search_service

    _RecordingFacade.calls.clear()
    project_id = uuid.uuid4()
    key_id = uuid.uuid4()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, provider)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(facade_module, "AgentsFacade", _RecordingFacade)

    await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    seeded_hosts = {c["hostname"] for c in _RecordingFacade.calls}
    other_hosts = set(SEARCH_PROVIDER_HOSTS.values()) - {expected_host}
    assert seeded_hosts == {expected_host}
    assert not seeded_hosts & other_hosts


@pytest.mark.asyncio
async def test_activate_fails_closed_when_allowlist_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    project_id = uuid.uuid4()
    key_id = uuid.uuid4()
    redis = _FakeRedis()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, SearchProvider.TAVILY)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: redis)
    monkeypatch.setattr(facade_module, "AgentsFacade", _RaisingFacade)

    with pytest.raises(RuntimeError, match="allowlist write failed"):
        await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    # The non-transactional Redis publish must not fire once the transactional
    # allowlist write has failed (Q-5 — fail closed).
    assert redis.published == []
