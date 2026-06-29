"""RAG config create must enforce one embedding dimension per project (§21.4)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.domain.errors import EmbedDimensionConflict
from contexts.knowledge.domain.models import RagConfigDraft


def _draft(provider: str, model: str) -> RagConfigDraft:
    return RagConfigDraft(
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),  # only .value is read in audit
        chunk_params={},
        embed_key_id=None,
        embed_provider=provider,
        embed_model=model,
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=5,
    )


def _svc(existing: list) -> RagConfigService:
    svc = RagConfigService(db=AsyncMock())
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = existing
    return svc


@pytest.mark.asyncio
async def test_create_rejects_mismatched_dimension() -> None:
    # Existing config embeds at 1536 (openai small); new one is 768 (gemini).
    existing = [SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")]
    svc = _svc(existing)
    with pytest.raises(EmbedDimensionConflict):
        await svc.create(
            project_id=uuid.uuid4(),
            draft=_draft("gemini", "text-embedding-004"),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_create_allows_matching_dimension() -> None:
    # Both 1536-dim (openai small) -> allowed.
    existing = [SimpleNamespace(embed_provider="openai", embed_model="text-embedding-3-small")]
    svc = _svc(existing)
    created = SimpleNamespace(
        id=uuid.uuid4(),
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=False,
    )
    svc._configs.create.return_value = created
    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        out = await svc.create(
            project_id=uuid.uuid4(),
            draft=_draft("openai", "text-embedding-3-small"),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out is created
