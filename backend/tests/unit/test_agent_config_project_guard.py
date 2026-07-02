"""SEC-H1 regression — agents may only attach RAG / GraphRAG configs that
belong to their own project.

The IDOR was that ``AgentService.create``/``patch`` passed ``rag_config_id`` /
``graphrag_config_id`` straight through with no project check, so a member of
Project A could attach Project B's config and exfiltrate B's document chunks
at retrieval time (the Qdrant collection is keyed on the config's project_id).

These tests exercise the guard methods directly with a fake knowledge facade —
no DB needed — covering the three branches: same project (allowed), foreign
project (rejected), and missing/soft-deleted config (rejected). The GraphRAG
guard also covers R11.01: the agent's key_group_id must differ from its
GraphRAG config's builder_key_group_id.
"""

from __future__ import annotations

import types
import uuid

import pytest

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.errors import (
    GraphRagBuilderKeyGroupConflict,
    GraphRagConfigOutOfProject,
    RagConfigOutOfProject,
)


class _FakeKnowledge:
    def __init__(self, *, rag: object | None = None, graph: object | None = None) -> None:
        self._rag = rag
        self._graph = graph

    async def get_rag_config(self, _config_id: uuid.UUID, *, include_deleted: bool = False) -> object | None:
        return self._rag

    async def get_graphrag_config(
        self, _config_id: uuid.UUID, *, include_deleted: bool = False
    ) -> object | None:
        return self._graph


def _svc(knowledge: _FakeKnowledge) -> AgentService:
    # Bypass __init__ (which would build real DB-backed repos); the guard
    # methods only touch self._knowledge.
    svc = AgentService.__new__(AgentService)
    svc._knowledge = knowledge  # type: ignore[attr-defined]
    return svc


def _cfg(project_id: uuid.UUID, builder_key_group_id: uuid.UUID | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        project_id=project_id,
        builder_key_group_id=builder_key_group_id or uuid.uuid4(),
    )


# ---- RAG ----------------------------------------------------------------


async def test_rag_same_project_allowed() -> None:
    pid = uuid.uuid4()
    svc = _svc(_FakeKnowledge(rag=_cfg(pid)))
    await svc._assert_rag_config_in_project(rag_config_id=uuid.uuid4(), project_id=pid)


async def test_rag_cross_project_rejected() -> None:
    svc = _svc(_FakeKnowledge(rag=_cfg(uuid.uuid4())))
    with pytest.raises(RagConfigOutOfProject):
        await svc._assert_rag_config_in_project(rag_config_id=uuid.uuid4(), project_id=uuid.uuid4())


async def test_rag_missing_rejected() -> None:
    svc = _svc(_FakeKnowledge(rag=None))
    with pytest.raises(RagConfigOutOfProject):
        await svc._assert_rag_config_in_project(rag_config_id=uuid.uuid4(), project_id=uuid.uuid4())


# ---- GraphRAG -----------------------------------------------------------


async def test_graphrag_same_project_allowed() -> None:
    pid = uuid.uuid4()
    svc = _svc(_FakeKnowledge(graph=_cfg(pid)))
    await svc._assert_graphrag_config_compatible(
        graphrag_config_id=uuid.uuid4(), project_id=pid, key_group_id=uuid.uuid4()
    )


async def test_graphrag_cross_project_rejected() -> None:
    svc = _svc(_FakeKnowledge(graph=_cfg(uuid.uuid4())))
    with pytest.raises(GraphRagConfigOutOfProject):
        await svc._assert_graphrag_config_compatible(
            graphrag_config_id=uuid.uuid4(), project_id=uuid.uuid4(), key_group_id=uuid.uuid4()
        )


async def test_graphrag_missing_rejected() -> None:
    svc = _svc(_FakeKnowledge(graph=None))
    with pytest.raises(GraphRagConfigOutOfProject):
        await svc._assert_graphrag_config_compatible(
            graphrag_config_id=uuid.uuid4(), project_id=uuid.uuid4(), key_group_id=uuid.uuid4()
        )


# ---- R11.01 builder/consumer key-group split -----------------------------


async def test_graphrag_builder_key_group_conflict_rejected() -> None:
    pid = uuid.uuid4()
    shared_kg = uuid.uuid4()
    svc = _svc(_FakeKnowledge(graph=_cfg(pid, builder_key_group_id=shared_kg)))
    with pytest.raises(GraphRagBuilderKeyGroupConflict):
        await svc._assert_graphrag_config_compatible(
            graphrag_config_id=uuid.uuid4(), project_id=pid, key_group_id=shared_kg
        )


async def test_graphrag_distinct_key_groups_allowed() -> None:
    pid = uuid.uuid4()
    svc = _svc(_FakeKnowledge(graph=_cfg(pid, builder_key_group_id=uuid.uuid4())))
    await svc._assert_graphrag_config_compatible(
        graphrag_config_id=uuid.uuid4(), project_id=pid, key_group_id=uuid.uuid4()
    )


# ---- require_exists=False (implicit recheck of an already-attached config) --


async def test_graphrag_missing_skipped_when_not_required() -> None:
    # A config that was soft-deleted out from under the agent must not turn
    # an unrelated field edit into a 404 when the caller isn't attaching it.
    svc = _svc(_FakeKnowledge(graph=None))
    await svc._assert_graphrag_config_compatible(
        graphrag_config_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        require_exists=False,
    )


async def test_graphrag_cross_project_skipped_when_not_required() -> None:
    svc = _svc(_FakeKnowledge(graph=_cfg(uuid.uuid4())))
    await svc._assert_graphrag_config_compatible(
        graphrag_config_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        require_exists=False,
    )


async def test_graphrag_builder_conflict_still_enforced_when_not_required() -> None:
    # require_exists only relaxes the existence/project check -- the R11.01
    # conflict itself must still be enforced once the config is found.
    pid = uuid.uuid4()
    shared_kg = uuid.uuid4()
    svc = _svc(_FakeKnowledge(graph=_cfg(pid, builder_key_group_id=shared_kg)))
    with pytest.raises(GraphRagBuilderKeyGroupConflict):
        await svc._assert_graphrag_config_compatible(
            graphrag_config_id=uuid.uuid4(),
            project_id=pid,
            key_group_id=shared_kg,
            require_exists=False,
        )
