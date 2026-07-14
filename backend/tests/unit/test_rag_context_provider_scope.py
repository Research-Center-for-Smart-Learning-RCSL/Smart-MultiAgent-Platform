"""F-1 RC-b degrade: a pinned RAG key not carried into the config's project
must not be billed, and retrieval degrades gracefully.

- Out-of-scope embed key  -> no RAG block (source absent) + one audit event.
- Out-of-scope rerank key -> vector-only retrieval + one audit event.

Each degradation emits exactly one audit event whose metadata carries only
identifiers (config/key/project ids, capability) — never a key secret.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.knowledge.application.rag_context_provider import RagContextProvider


def _cfg(*, rerank: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        embed_key_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=rerank,
        rerank_key_id=uuid.uuid4() if rerank else None,
        rerank_model="rerank-3",
        top_k=5,
    )


def _scope_facade(scope_map: dict[uuid.UUID, bool]) -> MagicMock:
    facade = MagicMock()
    facade.is_key_in_project_scope = AsyncMock(side_effect=lambda kid, pid: scope_map.get(kid, True))
    return facade


def _assert_no_secret(metadata: dict) -> None:
    for key, value in metadata.items():
        assert "secret" not in key.lower()
        assert "api_key" not in key.lower()
        # The values are only ids/labels; assert nothing key-shaped leaked.
        assert not str(value).startswith("sk-")


class TestEmbedScopeDegrade:
    async def test_out_of_scope_embed_key_yields_no_block_and_audits(self) -> None:
        # The embed key's scope is enforced at the router chokepoint: retrieval
        # raises KeyProjectScopeError before any billed call. The provider drops
        # the RAG block for the turn and audits the degradation.
        from contexts.keys.domain.errors import KeyProjectScopeError

        cfg = _cfg(rerank=False)
        provider = RagContextProvider(AsyncMock(), router=MagicMock(), qdrant_url="http://qdrant")

        def _fake_retrieve_service(_db, *, embedder, qdrant, reranker):
            svc = MagicMock()
            svc.query = AsyncMock(
                side_effect=KeyProjectScopeError(key_id=cfg.embed_key_id, project_id=cfg.project_id)
            )
            return svc

        with (
            patch(
                "contexts.knowledge.infrastructure.repositories.RagConfigRepository",
                lambda _db: SimpleNamespace(get=AsyncMock(return_value=cfg)),
            ),
            patch(
                "contexts.knowledge.application.rag_context_provider.audit.emit",
                new_callable=AsyncMock,
            ) as emit,
            patch(
                "contexts.knowledge.infrastructure.embedders.router_embedder_for",
                return_value=MagicMock(),
            ),
            patch("qdrant_client.AsyncQdrantClient", return_value=AsyncMock()),
            patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", MagicMock()),
            patch("contexts.knowledge.application.retrieve.RetrieveService", _fake_retrieve_service),
        ):
            result = await provider.query(rag_config_id=cfg.id, query_text="hello", agent_id=uuid.uuid4())

        assert result is None  # RAG source absent
        emit.assert_awaited_once()
        event = emit.await_args.args[1]
        assert event.action == "rag.key_scope_degraded"
        assert event.metadata["capability"] == "embedding"
        assert event.metadata["key_id"] == str(cfg.embed_key_id)
        assert event.metadata["project_id"] == str(cfg.project_id)
        _assert_no_secret(event.metadata)


class TestRerankScopeDegrade:
    async def test_out_of_scope_rerank_key_falls_back_to_vector_only(self) -> None:
        cfg = _cfg(rerank=True)
        # Embed carried, rerank withdrawn.
        facade = _scope_facade({cfg.embed_key_id: True, cfg.rerank_key_id: False})
        provider = RagContextProvider(AsyncMock(), router=MagicMock(), qdrant_url="http://qdrant")

        captured: dict = {}

        def _fake_retrieve_service(_db, *, embedder, qdrant, reranker):
            captured["reranker"] = reranker
            svc = MagicMock()
            svc.query = AsyncMock(return_value=[])  # empty -> query() returns None
            return svc

        with (
            patch(
                "contexts.knowledge.infrastructure.repositories.RagConfigRepository",
                lambda _db: SimpleNamespace(get=AsyncMock(return_value=cfg)),
            ),
            patch("contexts.keys.interfaces.facade.KeysFacade", lambda _db: facade),
            patch(
                "contexts.knowledge.application.rag_context_provider.audit.emit",
                new_callable=AsyncMock,
            ) as emit,
            patch(
                "contexts.knowledge.infrastructure.embedders.router_embedder_for",
                return_value=MagicMock(),
            ),
            patch("qdrant_client.AsyncQdrantClient", return_value=AsyncMock()),
            patch("contexts.knowledge.infrastructure.qdrant_store.QdrantStore", MagicMock()),
            patch("contexts.knowledge.application.retrieve.RetrieveService", _fake_retrieve_service),
            # If the reranker were (wrongly) built, this would be used; assert it is not.
            patch("contexts.knowledge.infrastructure.rerankers.RouterReranker") as router_reranker,
        ):
            await provider.query(rag_config_id=cfg.id, query_text="hello", agent_id=uuid.uuid4())

        # Vector-only: retrieval ran with no reranker, and RouterReranker was never built.
        assert captured["reranker"] is None
        router_reranker.assert_not_called()
        emit.assert_awaited_once()
        event = emit.await_args.args[1]
        assert event.action == "rag.key_scope_degraded"
        assert event.metadata["capability"] == "rerank"
        assert event.metadata["key_id"] == str(cfg.rerank_key_id)
        _assert_no_secret(event.metadata)
