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
    async def publish(self, _channel: str, _message: str) -> int:
        return 1


def _key(project_id: uuid.UUID, key_id: uuid.UUID) -> SearchKey:
    return SearchKey(
        id=key_id,
        project_id=project_id,
        provider=SearchProvider.TAVILY,
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

    await service.activate(
        project_id=project_id,
        key_id=replacement_key_id,
        actor_user_id=uuid.uuid4(),
    )

    assert [(event.action, event.resource_id) for event in events] == [
        ("search_key.deactivated", deactivated_key_id),
        ("search_key.activated", replacement_key_id),
    ]
