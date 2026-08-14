"""F-3 — the durable retry sweep for configless collection teardowns (§8.4, AC-6).

The sweep is the backstop behind the delete path and the create-path retry: it
reclaims collections for projects nobody happens to reconfigure. These tests pin the
properties that make it safe to run unattended — it only touches pins that are
genuinely owed a teardown, one failure never aborts the pass, a pin is counted as
released only on a Qdrant-confirmed absence, and each pin is torn down in its own
transaction so the advisory locks do not accumulate across the pass.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.domain.embedding_pin import PinKind, TeardownOutcome
from contexts.knowledge.interfaces.facade import KnowledgeFacade

_PIN_REPO = "contexts.knowledge.infrastructure.embedding_pin_repository.EmbeddingPinRepository"
_RAG_SVC = "contexts.knowledge.application.config_service.RagConfigService"
_KNOWMAP_SVC = "contexts.knowledge.application.knowmap_config_service.KnowmapConfigService"
_GRAPHRAG_SVC = "contexts.knowledge.application.graphrag_config_service.GraphRagConfigService"


def _pin(project_id: uuid.UUID, kind: PinKind = PinKind.FILE_RAG) -> Any:
    return SimpleNamespace(project_id=project_id, kind=kind.value, dim=1536)


def _facade(*, configless: set[uuid.UUID]) -> KnowledgeFacade:
    """A facade whose File RAG repo reports every id outside ``configless`` as live."""
    facade = KnowledgeFacade(AsyncMock())
    for repo_attr in ("_configs", "_knowmap", "_graphrag"):
        repo = AsyncMock()
        repo.list_for_project.side_effect = lambda pid: [] if pid in configless else [object()]
        setattr(facade, repo_attr, repo)
    return facade


def _patch_sweep(pins: list[Any], service: Any) -> Any:
    pin_repo = AsyncMock()
    pin_repo.list_all.return_value = pins
    return (
        patch(_PIN_REPO, return_value=pin_repo),
        patch(_RAG_SVC, return_value=service),
        patch(_KNOWMAP_SVC, return_value=service),
        patch(_GRAPHRAG_SVC, return_value=service),
    )


@pytest.mark.asyncio
async def test_candidates_exclude_pins_with_live_configs() -> None:
    # AC-6: a pin whose project still has a live config is not an orphan — it must
    # not become a teardown candidate at all.
    live, orphan = uuid.uuid4(), uuid.uuid4()
    facade = _facade(configless={orphan})
    p1, p2, p3, p4 = _patch_sweep([_pin(live), _pin(orphan)], AsyncMock())
    with p1, p2, p3, p4:
        owed = await facade.list_pending_collection_teardowns(limit=50)
    assert owed == [(orphan, PinKind.FILE_RAG)]


@pytest.mark.asyncio
async def test_candidates_cover_all_three_kinds() -> None:
    # AC-6: File RAG, Knowledge Map, and Concept Map all copied the bug, so the
    # sweep must reclaim all three kinds of pin.
    ids = {kind: uuid.uuid4() for kind in PinKind}
    facade = _facade(configless=set(ids.values()))
    p1, p2, p3, p4 = _patch_sweep([_pin(pid, kind) for kind, pid in ids.items()], AsyncMock())
    with p1, p2, p3, p4:
        owed = await facade.list_pending_collection_teardowns(limit=50)
    assert sorted(k.value for _pid, k in owed) == sorted(k.value for k in PinKind)


@pytest.mark.asyncio
async def test_candidates_respect_the_limit() -> None:
    # AC-6: the pass is bounded — a backlog drains over several passes rather than
    # one unbounded pass holding the worker (and, before the batching fix, a lock per
    # pin) for as long as it takes.
    orphans = [uuid.uuid4() for _ in range(5)]
    facade = _facade(configless=set(orphans))
    p1, p2, p3, p4 = _patch_sweep([_pin(o) for o in orphans], AsyncMock())
    with p1, p2, p3, p4:
        owed = await facade.list_pending_collection_teardowns(limit=2)
    assert len(owed) == 2


@pytest.mark.asyncio
async def test_retry_delegates_to_the_kind_specific_service() -> None:
    orphan = uuid.uuid4()
    facade = _facade(configless={orphan})
    svc = AsyncMock()
    svc.teardown_orphan_collection.return_value = TeardownOutcome.DROPPED
    p1, p2, p3, p4 = _patch_sweep([], svc)
    with p1, p2, p3, p4:
        outcome = await facade.retry_collection_teardown(orphan, PinKind.KNOWMAP)
    assert outcome is TeardownOutcome.DROPPED
    svc.teardown_orphan_collection.assert_awaited_once_with(project_id=orphan)


@pytest.mark.asyncio
async def test_policy_isolates_per_item_failure_and_uses_a_session_per_pin() -> None:
    # AC-6: one project's teardown raising must not abort the pass; and each pin must
    # get its own transaction, or the transaction-scoped advisory locks accumulate
    # across the whole sweep and block config creation for every project touched.
    import app.workers.tasks.retention as ret

    first, boom, last = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owed = [(first, PinKind.FILE_RAG), (boom, PinKind.FILE_RAG), (last, PinKind.FILE_RAG)]
    sessions_opened = 0

    class _SessionCM:
        async def __aenter__(self) -> Any:
            nonlocal sessions_opened
            sessions_opened += 1
            session = AsyncMock()
            session.begin = _Begin
            return session

        async def __aexit__(self, *_a: object) -> None:
            return None

    class _Begin:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_a: object) -> None:
            return None

    async def _retry(self: Any, project_id: uuid.UUID, kind: PinKind) -> TeardownOutcome:
        if project_id == boom:
            raise RuntimeError("connection reset")
        return TeardownOutcome.DROPPED

    with (
        patch.object(ret, "get_sessionmaker", return_value=_SessionCM),
        patch.object(KnowledgeFacade, "list_pending_collection_teardowns", AsyncMock(return_value=owed)),
        patch.object(KnowledgeFacade, "retry_collection_teardown", _retry),
        patch.object(ret, "_emit_summary", AsyncMock()),
    ):
        released = await ret._retry_pending_collection_teardowns(AsyncMock())

    assert released == 2  # first + last survived the middle one blowing up
    assert sessions_opened == 3  # one transaction per pin, so each lock releases


@pytest.mark.asyncio
async def test_failed_teardown_is_not_counted_as_released() -> None:
    # AC-6/AC-4: a Qdrant error keeps the pin, so the sweep reports nothing released
    # and the next pass retries it.
    orphan = uuid.uuid4()
    facade = _facade(configless={orphan})
    svc = AsyncMock()
    svc.teardown_orphan_collection.return_value = TeardownOutcome.FAILED
    p1, p2, p3, p4 = _patch_sweep([], svc)
    with p1, p2, p3, p4:
        outcome = await facade.retry_collection_teardown(orphan, PinKind.FILE_RAG)
    assert outcome.pin_released is False
