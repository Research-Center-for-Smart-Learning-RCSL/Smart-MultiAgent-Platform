"""F-1 RC-a: rerank save-validation must enforce project carried-key scope.

A pinned rerank key may be attached to a RAG config only while it is carried
into the config's project (``key_projects.carried = true``). Mirrors the
embedding-key scope gate that ``_validate_embed_key`` already applies.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.config_service import RagConfigService
from contexts.knowledge.domain.errors import CapabilityMismatch
from contexts.knowledge.domain.models import RagConfigDraft


def _cohere_key() -> SimpleNamespace:
    # Only ``.provider.value`` is read by the validator.
    return SimpleNamespace(provider=SimpleNamespace(value="cohere"))


def _svc(*, in_scope: bool) -> tuple[RagConfigService, AsyncMock]:
    svc = RagConfigService(db=AsyncMock())
    svc._configs = AsyncMock()
    svc._configs.list_for_project.return_value = []
    svc._pins = AsyncMock()  # F-11: stub the durable pin ensure added to create()
    keys = AsyncMock()
    keys.get_key.return_value = _cohere_key()
    keys.is_key_in_project_scope.return_value = in_scope
    svc._keys_facade = keys
    return svc, keys


def _draft(*, rerank_key_id: uuid.UUID) -> RagConfigDraft:
    return RagConfigDraft(
        name="cfg",
        chunk_strategy=SimpleNamespace(value="fixed"),
        chunk_params={},
        embed_key_id=None,  # skip embed-key validation; isolate the rerank gate
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=True,
        rerank_key_id=rerank_key_id,
        rerank_provider="cohere",
        rerank_model="rerank-3",
        top_k=5,
    )


class TestRerankCreateScope:
    async def test_create_rejects_foreign_rerank_key(self) -> None:
        svc, keys = _svc(in_scope=False)
        project_id = uuid.uuid4()
        rerank_key_id = uuid.uuid4()
        with pytest.raises(CapabilityMismatch):
            await svc.create(
                project_id=project_id,
                draft=_draft(rerank_key_id=rerank_key_id),
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        keys.is_key_in_project_scope.assert_awaited_with(rerank_key_id, project_id)

    async def test_create_allows_carried_rerank_key(self) -> None:
        svc, _keys = _svc(in_scope=True)
        created = SimpleNamespace(
            id=uuid.uuid4(),
            name="cfg",
            chunk_strategy=SimpleNamespace(value="fixed"),
            embed_provider="openai",
            embed_model="text-embedding-3-small",
            rerank_enabled=True,
        )
        svc._configs.create.return_value = created
        with patch("contexts.knowledge.application.config_service.audit.emit", new=AsyncMock()):
            out = await svc.create(
                project_id=uuid.uuid4(),
                draft=_draft(rerank_key_id=uuid.uuid4()),
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        assert out is created


class TestRerankUpdateScope:
    def _existing_cfg(self, project_id: uuid.UUID) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project_id,
            rerank_enabled=True,
            rerank_key_id=uuid.uuid4(),
            rerank_provider="cohere",
        )

    async def test_update_rejects_foreign_rerank_key(self) -> None:
        svc, keys = _svc(in_scope=False)
        project_id = uuid.uuid4()
        cfg = self._existing_cfg(project_id)
        svc._configs.get.return_value = cfg
        new_key = uuid.uuid4()
        with pytest.raises(CapabilityMismatch):
            await svc.update(
                config_id=cfg.id,
                patch={"rerank_key_id": new_key, "rerank_provider": "cohere"},
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        keys.is_key_in_project_scope.assert_awaited_with(new_key, project_id)
