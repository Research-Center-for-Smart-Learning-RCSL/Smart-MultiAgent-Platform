"""F-4 FU-2 — project deletion cascades to Knowledge Map / Concept Map configs.

`ProjectService.soft_delete` cascaded to skills only, so a deleted project's graph
configs kept `deleted_at IS NULL` and every consumer that checked the config's own
column still saw them as live. Harmless while all build triggers sat behind a
membership check a deleted project already fails; not harmless once a background sweep
began enqueueing builds with no request behind it.

Restore is covered here too because a delete-only cascade would silently make project
restore lose the project's graphs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "").lower()


class _Result:
    rowcount = 1


class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()


_REPOS = [KnowmapConfigRepository, GraphRagConfigRepository]
_TABLE = {KnowmapConfigRepository: "knowmap_configs", GraphRagConfigRepository: "graphrag_configs"}


@pytest.mark.parametrize("repo_cls", _REPOS)
@pytest.mark.asyncio
async def test_cascade_delete_stamps_the_projects_instant(repo_cls: Any) -> None:
    db = _CaptureSession()
    project_id = uuid.uuid4()
    when = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    await repo_cls(db).soft_delete_for_project(project_id, when)

    sql = _sql(db.statements[0])
    assert _TABLE[repo_cls] in sql
    assert f"project_id='{project_id.hex}'" in sql
    # Only live rows: one already deleted keeps its own timestamp, which is what
    # lets the restore below tell the two apart.
    assert "deleted_atisnull" in sql
    assert "2026-07-2012:00:00" in sql


@pytest.mark.parametrize("repo_cls", _REPOS)
@pytest.mark.asyncio
async def test_cascade_restore_matches_only_that_deletions_rows(repo_cls: Any) -> None:
    db = _CaptureSession()
    project_id = uuid.uuid4()
    when = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    await repo_cls(db).restore_for_project(project_id, when)

    sql = _sql(db.statements[0])
    # Equality on the instant, not "IS NOT NULL": a config the user deleted on its
    # own beforehand carries a different timestamp and must stay deleted, or
    # restoring a project would resurrect discarded work.
    assert "deleted_at='2026-07-2012:00:00" in sql
    assert "deleted_atisnotnull" not in sql


@pytest.mark.asyncio
async def test_graphrag_cascade_preserves_owner_columns() -> None:
    # GraphRagConfigRepository.soft_delete clears the owner columns so the
    # owner-scoped partial unique indexes do not block a later create. That is
    # irreversible, so the cascade must not do it, or restore could not bring the
    # config back intact. Safe here: the whole project is gone, so no live owner
    # remains to want the slot.
    db = _CaptureSession()
    await GraphRagConfigRepository(db).soft_delete_for_project(uuid.uuid4(), datetime.now(UTC))

    sql = _sql(db.statements[0])
    assert "owner_chatroom_id" not in sql
    assert "owner_agent_group_id" not in sql
    assert "owner_workspace_id" not in sql


@pytest.mark.asyncio
async def test_project_soft_delete_drives_the_knowledge_cascade() -> None:
    from contexts.tenancy.application.project_service import ProjectService

    project_id = uuid.uuid4()
    when = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    svc = ProjectService.__new__(ProjectService)
    calls: dict[str, Any] = {}

    class _Projects:
        async def soft_delete(self, pid: uuid.UUID) -> datetime:
            calls["deleted"] = pid
            return when

    class _Skills:
        async def cascade_owner_deleted(self, *, scope: Any, owner_id: uuid.UUID) -> list[uuid.UUID]:
            return []

    class _Knowledge:
        async def cascade_project_deleted(
            self, *, project_id: uuid.UUID, deleted_at: datetime
        ) -> dict[str, int]:
            calls["cascade"] = (project_id, deleted_at)
            return {"knowmap_configs": 2, "graphrag_configs": 1}

    svc._db = None  # type: ignore[assignment]
    svc._projects = _Projects()  # type: ignore[assignment]
    svc._skills = _Skills()  # type: ignore[assignment]
    svc._knowledge = _Knowledge()  # type: ignore[assignment]

    emitted: dict[str, Any] = {}

    async def _emit(_db: Any, event: Any) -> None:
        emitted["metadata"] = event.metadata

    import contexts.tenancy.application.project_service as mod

    original = mod.audit.emit
    mod.audit.emit = _emit  # type: ignore[assignment]
    try:
        await svc.soft_delete(
            project_id=project_id, actor_user_id=uuid.uuid4(), actor_ip=None, request_id=None
        )
    finally:
        mod.audit.emit = original  # type: ignore[assignment]

    # The cascade runs with the project's own timestamp, not a fresh one.
    assert calls["cascade"] == (project_id, when)
    # And the counts land in the audit trail, so the deletion is accountable.
    assert emitted["metadata"]["knowmap_configs"] == 2
    assert emitted["metadata"]["graphrag_configs"] == 1
