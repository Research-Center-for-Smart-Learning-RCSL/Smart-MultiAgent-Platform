"""Expired knowledge-ingest leases are safely re-offered."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks.knowledge_ingest import knowledge_ingest_reconcile
from contexts.knowledge.domain.models import DocumentStatus, IngestClaim

_MOD = "app.workers.tasks.knowledge_ingest"


def _claim(attempt: int) -> IngestClaim:
    return IngestClaim(
        attempt=attempt,
        token=uuid.uuid4(),
        until=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )


def _sessionmaker(db: AsyncMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=db)
    context.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=context)


@pytest.mark.asyncio
async def test_expired_claims_are_reclaimed_and_enqueued_with_new_ownership() -> None:
    rag_id, knowmap_id = uuid.uuid4(), uuid.uuid4()
    rag_claim, knowmap_claim = _claim(3), _claim(5)
    rag_repo = AsyncMock()
    rag_repo.list_expired_claim_ids.return_value = [rag_id]
    rag_repo.claim_for_reingest.return_value = rag_claim
    knowmap_repo = AsyncMock()
    knowmap_repo.list_expired_claim_ids.return_value = [knowmap_id]
    knowmap_repo.claim_for_reingest.return_value = knowmap_claim
    db = AsyncMock()

    with (
        patch(f"{_MOD}.get_sessionmaker", return_value=_sessionmaker(db)),
        patch(f"{_MOD}.RagDocumentRepository", return_value=rag_repo),
        patch(f"{_MOD}.KnowmapDocumentRepository", return_value=knowmap_repo),
        patch(f"{_MOD}._enqueue_owned", new=AsyncMock()) as enqueue_owned,
    ):
        recovered = await knowledge_ingest_reconcile({})

    assert recovered == 2
    assert enqueue_owned.await_count == 2
    assert enqueue_owned.await_args_list[0].kwargs["claim"] is rag_claim
    assert enqueue_owned.await_args_list[1].kwargs["claim"] is knowmap_claim
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_reenqueue_failure_only_fails_the_current_claim() -> None:
    rag_id = uuid.uuid4()
    claim = _claim(2)
    rag_repo = AsyncMock()
    rag_repo.list_expired_claim_ids.return_value = [rag_id]
    rag_repo.claim_for_reingest.return_value = claim
    knowmap_repo = AsyncMock()
    knowmap_repo.list_expired_claim_ids.return_value = []
    db = AsyncMock()

    with (
        patch(f"{_MOD}.get_sessionmaker", return_value=_sessionmaker(db)),
        patch(f"{_MOD}.RagDocumentRepository", return_value=rag_repo),
        patch(f"{_MOD}.KnowmapDocumentRepository", return_value=knowmap_repo),
        patch(
            f"{_MOD}._enqueue_owned",
            new=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        ),
    ):
        recovered = await knowledge_ingest_reconcile({})

    assert recovered == 0
    rag_repo.finish_claim.assert_awaited_once_with(
        document_id=rag_id,
        claim=claim,
        status=DocumentStatus.FAILED,
    )

