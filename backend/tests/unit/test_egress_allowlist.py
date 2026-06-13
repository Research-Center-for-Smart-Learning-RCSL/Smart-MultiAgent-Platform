"""Unit tests for :class:`EgressAllowlistService` — E.9."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.agents.application.egress_allowlist_service import (
    EgressAllowlistService,
)
from contexts.agents.domain.mcp import EgressAllowlistEntry


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)

        class _R:
            def first(self: Any) -> None:
                return None

            def all(self: Any) -> list[Any]:
                return []

            def one(self: Any) -> None:
                return None

        return _R()


class _FakeRepo:
    def __init__(self) -> None:
        self.entries: dict[tuple[uuid.UUID, str], EgressAllowlistEntry] = {}
        self.replaced_with: list[tuple[uuid.UUID, tuple[str, ...]]] = []
        self.deleted: list[tuple[uuid.UUID, str]] = []

    async def list_for_project(self, project_id: uuid.UUID):
        return [e for (p, _h), e in self.entries.items() if p == project_id]

    async def upsert(
        self,
        *,
        project_id: uuid.UUID,
        hostname: str,
        added_by_user_id: uuid.UUID | None,
        note: str | None,
    ) -> EgressAllowlistEntry:
        key = (project_id, hostname)
        existing = self.entries.get(key)
        if existing is not None:
            return existing
        entry = EgressAllowlistEntry(
            id=uuid.uuid4(),
            project_id=project_id,
            hostname=hostname,
            added_by_user_id=added_by_user_id,
            added_at=datetime.now(tz=UTC),
            note=note,
        )
        self.entries[key] = entry
        return entry

    async def delete(self, *, project_id: uuid.UUID, hostname: str) -> bool:
        key = (project_id, hostname)
        if key in self.entries:
            del self.entries[key]
            self.deleted.append(key)
            return True
        return False

    async def replace(
        self,
        *,
        project_id: uuid.UUID,
        hostnames,
        added_by_user_id: uuid.UUID | None,
    ):
        names = tuple(hostnames)
        self.replaced_with.append((project_id, names))
        # Clear existing for this project and reinsert.
        for key in [k for k in self.entries if k[0] == project_id]:
            del self.entries[key]
        for h in names:
            await self.upsert(
                project_id=project_id,
                hostname=h,
                added_by_user_id=added_by_user_id,
                note=None,
            )
        return await self.list_for_project(project_id)


@pytest.mark.asyncio
async def test_add_normalises_and_audits() -> None:
    session = _FakeSession()
    svc = EgressAllowlistService(session)  # type: ignore[arg-type]
    repo = _FakeRepo()
    svc._repo = repo  # type: ignore[assignment]

    entry = await svc.add(
        project_id=uuid.uuid4(),
        hostname="API.EXAMPLE.com",
        note="n",
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    assert entry.hostname == "api.example.com"
    # audit.emit produced exactly one insert
    assert session.executed


@pytest.mark.asyncio
async def test_add_rejects_invalid_hostname() -> None:
    svc = EgressAllowlistService(_FakeSession())  # type: ignore[arg-type]
    svc._repo = _FakeRepo()  # type: ignore[assignment]
    with pytest.raises(ValueError):
        await svc.add(
            project_id=uuid.uuid4(),
            hostname="not a host!!",
            note=None,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_replace_normalises_and_deduplicates() -> None:
    svc = EgressAllowlistService(_FakeSession())  # type: ignore[arg-type]
    repo = _FakeRepo()
    svc._repo = repo  # type: ignore[assignment]
    project_id = uuid.uuid4()
    entries = await svc.replace(
        project_id=project_id,
        hostnames=["Foo.example.com", "bar.example.com", "foo.example.com"],
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    # Deduped and normalised.
    hosts = sorted(e.hostname for e in entries)
    assert hosts == ["bar.example.com", "foo.example.com"]


@pytest.mark.asyncio
async def test_remove_returns_false_when_missing() -> None:
    svc = EgressAllowlistService(_FakeSession())  # type: ignore[arg-type]
    svc._repo = _FakeRepo()  # type: ignore[assignment]
    removed = await svc.remove(
        project_id=uuid.uuid4(),
        hostname="api.example.com",
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    assert removed is False


@pytest.mark.asyncio
async def test_non_owner_guard_is_router_concern_not_service() -> None:
    """The service trusts the caller; the guard lives in the router.

    This test pins the contract: no role-check inside the service itself, so
    the router is the only authorisation seam (matches R10.10-style rag).
    """
    svc = EgressAllowlistService(_FakeSession())  # type: ignore[arg-type]
    svc._repo = _FakeRepo()  # type: ignore[assignment]
    # Any caller can call the service — it is the router's job to gate owners.
    entry = await svc.add(
        project_id=uuid.uuid4(),
        hostname="api.example.com",
        note=None,
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    assert entry.hostname == "api.example.com"
