"""Orphaned pre-0069 ingests must still be reclaimable.

Moving rag/knowmap scan and ingest onto dedicated queues leaves any job the
previous release enqueued sitting on the default queue. The default worker has
no handler for them -- deliberately, because those jobs are memory-heavy and
`test_knowledge_worker_queues` pins them off the general worker -- so they fail
as an unknown function and their documents stay `ingesting`.

The reconciler is the safety net for exactly that, but both of its queries
required a non-NULL `ingest_claim_until`, and rows predating migration 0069
have NULL. These tests pin the widened predicates: the sweep must see such a
row, and `claim_for_reingest` must be able to claim it.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapDocumentRepository
from contexts.knowledge.infrastructure.repositories import RagDocumentRepository


def _empty_result() -> MagicMock:
    result = MagicMock()
    result.all.return_value = []
    result.first.return_value = None
    return result


def _compiled(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def _sweep_sql(repo_cls: type) -> str:
    db = AsyncMock()
    db.execute.side_effect = [_empty_result()]
    await repo_cls(db).list_expired_claim_ids(limit=10)
    return _compiled(db.execute.await_args_list[0].args[0])


async def _claim_sql(repo_cls: type) -> str:
    db = AsyncMock()
    db.execute.side_effect = [_empty_result()]
    await repo_cls(db).claim_for_reingest(uuid.uuid4())
    return _compiled(db.execute.await_args_list[0].args[0])


class TestRagLegacyClaimRecovery:
    async def test_sweep_sees_claimless_rows(self) -> None:
        sql = await _sweep_sql(RagDocumentRepository)

        assert "ingest_claim_until IS NULL" in sql
        assert "uploaded_at" in sql

    async def test_claim_for_reingest_accepts_claimless_rows(self) -> None:
        sql = await _claim_sql(RagDocumentRepository)

        assert "ingest_claim_until IS NULL" in sql
        assert "uploaded_at" in sql


class TestKnowmapLegacyClaimRecovery:
    async def test_sweep_sees_claimless_rows(self) -> None:
        sql = await _sweep_sql(KnowmapDocumentRepository)

        assert "ingest_claim_until IS NULL" in sql
        assert "uploaded_at" in sql

    async def test_claim_for_reingest_accepts_claimless_rows(self) -> None:
        sql = await _claim_sql(KnowmapDocumentRepository)

        assert "ingest_claim_until IS NULL" in sql
        assert "uploaded_at" in sql
