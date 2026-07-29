"""Recover knowledge documents whose ingest owner disappeared."""

from __future__ import annotations

import logging
from typing import Any

from contexts.knowledge.domain.models import DocumentStatus, IngestClaim
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapDocumentRepository
from contexts.knowledge.infrastructure.repositories import RagDocumentRepository
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.queue import enqueue

_log = logging.getLogger(__name__)
_SWEEP_LIMIT = 100


async def _enqueue_owned(
    *,
    name: str,
    prefix: str,
    document_id: str,
    claim: IngestClaim,
) -> None:
    await enqueue(
        name,
        document_id=document_id,
        ingest_attempt=claim.attempt,
        claim_token=str(claim.token),
        _job_id=f"{prefix}:{document_id}:{claim.attempt}",
    )


async def knowledge_ingest_reconcile(ctx: dict[str, Any]) -> int:
    """Reclaim expired RAG/Knowledge Map leases and offer their jobs again."""
    _ = ctx
    sm = get_sessionmaker()
    recovered = 0

    async with sm() as db:
        rag_repo = RagDocumentRepository(db)
        rag_ids = await rag_repo.list_expired_claim_ids(limit=_SWEEP_LIMIT)
        for document_id in rag_ids:
            claim = await rag_repo.claim_for_reingest(document_id)
            if claim is None:
                continue
            await db.commit()
            try:
                await _enqueue_owned(
                    name="rag_ingest_document",
                    prefix="rag-ingest",
                    document_id=str(document_id),
                    claim=claim,
                )
                recovered += 1
            except Exception:
                _log.exception("failed to re-enqueue expired RAG ingest %s", document_id)
                await rag_repo.finish_claim(
                    document_id=document_id,
                    claim=claim,
                    status=DocumentStatus.FAILED,
                )
                await db.commit()

        knowmap_repo = KnowmapDocumentRepository(db)
        knowmap_ids = await knowmap_repo.list_expired_claim_ids(limit=_SWEEP_LIMIT)
        for document_id in knowmap_ids:
            claim = await knowmap_repo.claim_for_reingest(document_id)
            if claim is None:
                continue
            await db.commit()
            try:
                await _enqueue_owned(
                    name="knowmap_ingest_document",
                    prefix="knowmap-ingest",
                    document_id=str(document_id),
                    claim=claim,
                )
                recovered += 1
            except Exception:
                _log.exception("failed to re-enqueue expired Knowledge Map ingest %s", document_id)
                await knowmap_repo.finish_claim(
                    document_id=document_id,
                    claim=claim,
                    status=DocumentStatus.FAILED,
                )
                await db.commit()

    if recovered:
        _log.info("recovered %d expired knowledge ingest claim(s)", recovered)
    return recovered


knowledge_ingest_reconcile.max_tries = 1  # type: ignore[attr-defined]

__all__ = ["knowledge_ingest_reconcile"]
