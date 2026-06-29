"""`CarryService.projects_for_key` owner-only gate (R7.03).

The reverse "which projects use this key" view must never leak a key's
project footprint to a non-owner. Ownership is enforced in the service before
the repository is ever consulted; these tests pin that contract with fakes so
no DB is required.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.keys.application.carry_service import CarryService
from contexts.keys.domain.errors import KeyNotFound, KeyNotOwnedByCaller
from contexts.keys.infrastructure.carry_repository import KeyProjectUsage


class _Key:
    def __init__(self, owner_user_id: uuid.UUID) -> None:
        self.owner_user_id = owner_user_id


class _FakeKeysRepo:
    def __init__(self, key: _Key | None) -> None:
        self._key = key

    async def get_active(self, _key_id: uuid.UUID) -> _Key | None:
        return self._key


class _FakeCarriesRepo:
    def __init__(self, usages: list[KeyProjectUsage]) -> None:
        self._usages = usages
        self.called = False

    async def list_projects_for_key(self, _key_id: uuid.UUID) -> list[KeyProjectUsage]:
        self.called = True
        return self._usages


def _service(keys_repo: _FakeKeysRepo, carries_repo: _FakeCarriesRepo) -> CarryService:
    svc = CarryService.__new__(CarryService)
    svc._keys = keys_repo  # type: ignore[attr-defined]
    svc._carries = carries_repo  # type: ignore[attr-defined]
    return svc


async def test_owner_sees_project_footprint() -> None:
    owner = uuid.uuid4()
    usage = KeyProjectUsage(project_id=uuid.uuid4(), carried_at=_now(), group_ids=(uuid.uuid4(),))
    carries = _FakeCarriesRepo([usage])
    svc = _service(_FakeKeysRepo(_Key(owner)), carries)

    result = await svc.projects_for_key(key_id=uuid.uuid4(), caller_user_id=owner)

    assert result == [usage]
    assert carries.called


async def test_missing_key_raises_not_found() -> None:
    carries = _FakeCarriesRepo([])
    svc = _service(_FakeKeysRepo(None), carries)

    with pytest.raises(KeyNotFound):
        await svc.projects_for_key(key_id=uuid.uuid4(), caller_user_id=uuid.uuid4())

    # Repository must not be touched once ownership fails closed.
    assert not carries.called


async def test_non_owner_is_rejected_before_repo() -> None:
    carries = _FakeCarriesRepo([])
    svc = _service(_FakeKeysRepo(_Key(uuid.uuid4())), carries)

    with pytest.raises(KeyNotOwnedByCaller):
        await svc.projects_for_key(key_id=uuid.uuid4(), caller_user_id=uuid.uuid4())

    assert not carries.called


def _now():
    from datetime import UTC, datetime

    return datetime.now(tz=UTC)
