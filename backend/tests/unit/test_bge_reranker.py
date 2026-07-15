"""F-19 (R10.08) — the keyless local BGE reranker path, end to end (backend).

[R10.08] promises two rerank options per RAG config: BYO-key ``cohere`` OR the
bundled keyless ``bge-reranker-v2-m3``. Before this fix the local path was closed
at every layer. These tests pin the reachable path:

* API schema accepts ``rerank_provider="bge"`` (and still rejects garbage);
* ``RagConfigService`` validates a keyless ``bge`` config WITHOUT a key when the
  bundled service URL is configured, and rejects it when the URL is empty or when
  a key is (wrongly) supplied — routing around the BYO-key / F-1 scope checks;
* the runtime factory builds a :class:`LocalBgeReranker` (not ``RouterReranker``)
  for a ``bge`` config, keyless.

The rerank-failure degrade (AC-4) is exercised in ``test_rag_services.py``
(``test_rerank_failure_degrades_to_vector_only``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.v1.rag import RagConfigCreateIn, RagConfigPatchIn
from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.domain.errors import CapabilityMismatch
from contexts.knowledge.domain.models import RagConfigDraft

_BGE_URL = "http://bge-reranker:80"


# --------------------------------------------------------------------------
# 1. API schema accepts "bge"
# --------------------------------------------------------------------------


def test_create_schema_accepts_bge() -> None:
    m = RagConfigCreateIn(
        name="c",
        chunk_strategy="fixed",
        embed_key_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=True,
        rerank_provider="bge",
    )
    assert m.rerank_provider == "bge"


def test_patch_schema_accepts_bge() -> None:
    assert RagConfigPatchIn(rerank_provider="bge").rerank_provider == "bge"


def test_create_schema_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        RagConfigCreateIn(
            name="c",
            chunk_strategy="fixed",
            embed_key_id=uuid.uuid4(),
            embed_provider="openai",
            embed_model="text-embedding-3-small",
            rerank_enabled=True,
            rerank_provider="voyage",  # not a rerank provider
        )


# --------------------------------------------------------------------------
# 2. Keyless validation branch (RagConfigService.create / update)
# --------------------------------------------------------------------------


def _draft(*, provider: str | None, key_id: uuid.UUID | None) -> RagConfigDraft:
    return RagConfigDraft(
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        chunk_params={},
        embed_key_id=None,  # skip embed-key validation; not under test here
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=True,
        rerank_key_id=key_id,
        rerank_provider=provider,
        rerank_model=None,
        top_k=5,
    )


def _create_svc(*, bge_url: str | None) -> RagConfigService:
    svc = RagConfigService(db=AsyncMock(), bge_reranker_url=bge_url)
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = []
    svc._pins = AsyncMock()
    svc._configs.create.return_value = SimpleNamespace(
        id=uuid.uuid4(),
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=True,
    )
    return svc


@pytest.mark.asyncio
async def test_create_bge_validates_without_key() -> None:
    svc = _create_svc(bge_url=_BGE_URL)
    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        out = await svc.create(
            project_id=uuid.uuid4(),
            draft=_draft(provider="bge", key_id=None),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert out.rerank_enabled is True


@pytest.mark.asyncio
async def test_create_bge_rejected_when_service_unconfigured() -> None:
    svc = _create_svc(bge_url="")  # local reranker not deployed
    with (
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(CapabilityMismatch),
    ):
        await svc.create(
            project_id=uuid.uuid4(),
            draft=_draft(provider="bge", key_id=None),
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


@pytest.mark.asyncio
async def test_create_bge_rejects_supplied_key() -> None:
    svc = _create_svc(bge_url=_BGE_URL)
    with (
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(CapabilityMismatch),
    ):
        await svc.create(
            project_id=uuid.uuid4(),
            draft=_draft(provider="bge", key_id=uuid.uuid4()),  # keyless provider must carry no key
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


def _update_svc(*, bge_url: str | None):
    cfg = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
    )
    svc = RagConfigService(db=AsyncMock(), bge_reranker_url=bge_url)
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.update.return_value = SimpleNamespace(project_id=cfg.project_id)
    return svc, cfg


@pytest.mark.asyncio
async def test_update_switch_to_bge_validates_without_key() -> None:
    svc, cfg = _update_svc(bge_url=_BGE_URL)
    with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
        await svc.update(
            config_id=cfg.id,
            patch={"rerank_enabled": True, "rerank_provider": "bge", "rerank_key_id": None},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    # The keyless selection was validated and the update applied (no key check).
    svc._configs.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_switch_to_bge_rejected_when_unconfigured() -> None:
    svc, cfg = _update_svc(bge_url="")
    with (
        patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()),
        pytest.raises(CapabilityMismatch),
    ):
        await svc.update(
            config_id=cfg.id,
            patch={"rerank_enabled": True, "rerank_provider": "bge", "rerank_key_id": None},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


# --------------------------------------------------------------------------
# 3. Runtime factory builds LocalBgeReranker for a bge config
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_builds_local_bge_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    from contexts.knowledge.application.rag_context_provider import RagContextProvider
    from contexts.knowledge.infrastructure.rerankers import LocalBgeReranker

    cfg = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        embed_key_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=True,
        rerank_provider="bge",
        rerank_key_id=None,
        rerank_model=None,
        top_k=8,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "contexts.knowledge.infrastructure.repositories.RagConfigRepository",
        lambda _db: SimpleNamespace(get=AsyncMock(return_value=cfg)),
    )
    monkeypatch.setattr(
        "contexts.knowledge.infrastructure.embedders.router_embedder_for",
        lambda **_kw: MagicMock(),
    )
    monkeypatch.setattr("qdrant_client.AsyncQdrantClient", lambda **_kw: AsyncMock())
    monkeypatch.setattr(
        "contexts.knowledge.infrastructure.qdrant_store.QdrantStore",
        lambda _c: MagicMock(),
    )

    class _FakeRetrieve:
        def __init__(self, _db: object, *, embedder: object, qdrant: object, reranker: object) -> None:
            captured["reranker"] = reranker

        async def query(self, **_kw: object) -> list[object]:
            return []

    monkeypatch.setattr("contexts.knowledge.application.retrieve.RetrieveService", _FakeRetrieve)

    provider = RagContextProvider(
        AsyncMock(),
        router=MagicMock(),
        qdrant_url="http://qdrant:6333",
        bge_reranker_url=_BGE_URL,
    )
    result = await provider.query(rag_config_id=cfg.id, query_text="hello", agent_id=uuid.uuid4())

    assert result is None  # empty retrieval → no block
    assert isinstance(captured.get("reranker"), LocalBgeReranker), (
        "a bge config must construct LocalBgeReranker, not RouterReranker, and must not "
        "fall through to vector-only when the service URL is configured"
    )
