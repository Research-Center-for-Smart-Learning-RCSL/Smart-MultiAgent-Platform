"""Unit tests for the GraphRAG owner->project invariant (Phase 2a D6, AC-9).

A project's builder must never be pointed at an owner in another tenant. The
guard dispatches per ``owner_kind`` (agent_group / workspace / chatroom) and
rejects an owner whose project differs from the config's, for every kind — even
the chatroom/workspace kinds Phase 1 does not yet exercise (Phase 2b adds those
owner surfaces).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.domain.errors import GraphRagOwnerProjectMismatch

_MISSING = object()

OWNER_KINDS = ["agent_group", "workspace", "chatroom"]


class _OwnerDb:
    """AsyncSession double: every owner-project query resolves to ``project_id``
    (or no row when ``_MISSING``)."""

    def __init__(self, project_id: Any) -> None:
        self._project_id = project_id

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        pid = self._project_id

        class _R:
            def first(_self) -> Any:  # noqa: N805
                if pid is _MISSING:
                    return None
                return SimpleNamespace(project_id=pid)

        return _R()


def _svc(project_id: Any) -> GraphRagConfigService:
    return GraphRagConfigService(_OwnerDb(project_id))  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", OWNER_KINDS)
@pytest.mark.asyncio
async def test_rejects_owner_in_another_project(kind: str) -> None:
    project = uuid.uuid4()
    other_project = uuid.uuid4()
    svc = _svc(other_project)
    with pytest.raises(GraphRagOwnerProjectMismatch):
        await svc._assert_owner_in_project(owner_kind=kind, owner_id=uuid.uuid4(), project_id=project)


@pytest.mark.parametrize("kind", OWNER_KINDS)
@pytest.mark.asyncio
async def test_allows_owner_in_same_project(kind: str) -> None:
    project = uuid.uuid4()
    svc = _svc(project)
    # No raise when the owner resolves to the config's project.
    await svc._assert_owner_in_project(owner_kind=kind, owner_id=uuid.uuid4(), project_id=project)


@pytest.mark.asyncio
async def test_rejects_missing_owner() -> None:
    svc = _svc(_MISSING)
    with pytest.raises(GraphRagOwnerProjectMismatch):
        await svc._assert_owner_in_project(
            owner_kind="agent_group", owner_id=uuid.uuid4(), project_id=uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_rejects_unknown_owner_kind() -> None:
    svc = _svc(uuid.uuid4())
    with pytest.raises(GraphRagOwnerProjectMismatch):
        await svc._assert_owner_in_project(
            owner_kind="galaxy", owner_id=uuid.uuid4(), project_id=uuid.uuid4()
        )
