from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.keys.application.search_service import SearchKeyService
from contexts.keys.domain.probe_status import ProbeStatus
from contexts.keys.domain.search import SearchKey, SearchProvider


class _FakeSession:
    pass


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


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


class _Repo:
    """Minimal ``SearchKeyRepository`` stand-in for a fixed provider's key."""

    def __init__(
        self,
        project_id: uuid.UUID,
        provider: SearchProvider = SearchProvider.TAVILY,
        *,
        deactivated: list[uuid.UUID] | None = None,
    ) -> None:
        self._project_id = project_id
        self._provider = provider
        self._deactivated = deactivated or []

    async def get_active(self, key_id: uuid.UUID) -> SearchKey | None:
        return _key(self._project_id, key_id, self._provider)

    async def atomic_activate(self, **_: Any) -> list[uuid.UUID]:
        return self._deactivated


async def _noop_emit(_db: Any, _event: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_activate_audits_each_deactivated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    project_id = uuid.uuid4()
    replacement_key_id = uuid.uuid4()
    deactivated_key_id = uuid.uuid4()
    events: list[Any] = []

    async def _emit(_db: Any, event: Any) -> None:
        events.append(event)

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, deactivated=[deactivated_key_id])  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _emit)
    monkeypatch.setattr(search_service, "get_redis", _FakeRedis)

    await service.activate(
        project_id=project_id,
        key_id=replacement_key_id,
        actor_user_id=uuid.uuid4(),
    )

    assert [(event.action, event.resource_id) for event in events] == [
        ("search_key.deactivated", deactivated_key_id),
        ("search_key.activated", replacement_key_id),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(SearchProvider))
async def test_activate_returns_the_activated_keys_provider(
    monkeypatch: pytest.MonkeyPatch, provider: SearchProvider
) -> None:
    """The router (app/api/v1/search_keys.py) seeds the egress allowlist from
    this return value -- see test_search_keys_activate_route.py. activate()
    itself must not reach into contexts.agents (that edge, combined with the
    pre-existing contexts.agents -> contexts.keys edge in web_search.py and
    the search adapters, would close a cycle between the two contexts)."""
    import contexts.keys.application.search_service as search_service

    project_id = uuid.uuid4()
    key_id = uuid.uuid4()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id, provider)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", _FakeRedis)

    result = await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    assert result is provider


@pytest.mark.asyncio
async def test_activate_publishes_after_all_audit_events(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.keys.application.search_service as search_service

    project_id = uuid.uuid4()
    key_id = uuid.uuid4()
    redis = _FakeRedis()

    service = SearchKeyService(_FakeSession())  # type: ignore[arg-type]
    service._repo = _Repo(project_id)  # type: ignore[assignment]
    monkeypatch.setattr(search_service.audit, "emit", _noop_emit)
    monkeypatch.setattr(search_service, "get_redis", lambda: redis)

    await service.activate(project_id=project_id, key_id=key_id, actor_user_id=uuid.uuid4())

    assert redis.published == [("search_key.activated", f"{project_id}:{key_id}")]
