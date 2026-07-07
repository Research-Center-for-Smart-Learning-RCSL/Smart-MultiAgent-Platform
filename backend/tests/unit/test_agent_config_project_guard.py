"""SEC-H1 regression — agents may only attach a RAG config that belongs to
their own project.

The IDOR was that ``AgentService.create``/``patch`` passed ``rag_config_id``
straight through with no project check, so a member of Project A could attach
Project B's config and exfiltrate B's document chunks at retrieval time (the
Qdrant collection is keyed on the config's project_id).

These tests exercise the guard method directly with a fake knowledge facade —
no DB needed — covering the three branches: same project (allowed), foreign
project (rejected), and missing/soft-deleted config (rejected).

GraphRAG has no analogous agent-side guard since Phase 1: agents no longer
reference a GraphRAG config (ownership moved to the discriminated owner model),
so the reverse attach path and its ``_assert_graphrag_config_compatible`` guard
were removed.
"""

from __future__ import annotations

import types
import uuid

import pytest

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.errors import RagConfigOutOfProject


class _FakeKnowledge:
    def __init__(self, *, rag: object | None = None) -> None:
        self._rag = rag

    async def get_rag_config(self, _config_id: uuid.UUID, *, include_deleted: bool = False) -> object | None:
        return self._rag


def _svc(knowledge: _FakeKnowledge) -> AgentService:
    # Bypass __init__ (which would build real DB-backed repos); the guard
    # method only touches self._knowledge.
    svc = AgentService.__new__(AgentService)
    svc._knowledge = knowledge  # type: ignore[attr-defined]
    return svc


def _cfg(project_id: uuid.UUID) -> types.SimpleNamespace:
    return types.SimpleNamespace(project_id=project_id)


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
