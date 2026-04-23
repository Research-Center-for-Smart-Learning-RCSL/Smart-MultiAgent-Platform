"""RAG config use-cases — CRUD with capability validation (R10.05)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.errors import CapabilityMismatch as KeysCapabilityMismatch
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
)
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from contexts.knowledge.domain.errors import (
    CapabilityMismatch,
    EmbedModelNotWhitelisted,
    RagConfigNotFound,
)
from contexts.knowledge.domain.models import (
    EMBED_MODEL_WHITELIST,
    ChunkStrategy,
    RagConfig,
    RagConfigDraft,
)
from contexts.knowledge.infrastructure.repositories import RagConfigRepository
from shared_kernel import audit

_EMBED = CapabilityRequirement(capability=ProviderCapability.EMBEDDING)
_RERANK = CapabilityRequirement(capability=ProviderCapability.RERANK)


class RagConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._keys = ApiKeyRepository(db)

    async def _validate_embed_key(
        self, *, key_id: uuid.UUID, provider: str
    ) -> ApiKey:
        key = await self._keys.get_active(key_id)
        if key is None:
            raise CapabilityMismatch(f"embed_key {key_id} missing or deleted")
        if key.provider.value != provider:
            raise CapabilityMismatch(
                f"embed_key provider {key.provider.value!r} != config provider {provider!r}"
            )
        try:
            assert_capability(ApiKeyProvider(key.provider.value), _EMBED)
        except KeysCapabilityMismatch as exc:
            raise CapabilityMismatch(str(exc)) from exc
        return key

    async def _validate_rerank_key(
        self, *, key_id: uuid.UUID, provider: str
    ) -> ApiKey:
        key = await self._keys.get_active(key_id)
        if key is None:
            raise CapabilityMismatch(f"rerank_key {key_id} missing or deleted")
        if key.provider.value != provider:
            raise CapabilityMismatch(
                f"rerank_key provider {key.provider.value!r} != {provider!r}"
            )
        try:
            assert_capability(ApiKeyProvider(key.provider.value), _RERANK)
        except KeysCapabilityMismatch as exc:
            raise CapabilityMismatch(str(exc)) from exc
        return key

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: RagConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagConfig:
        # R10.05 whitelist.
        if (draft.embed_provider, draft.embed_model) not in EMBED_MODEL_WHITELIST:
            raise EmbedModelNotWhitelisted(
                f"{draft.embed_provider}:{draft.embed_model} not whitelisted"
            )

        if draft.embed_key_id is not None:
            await self._validate_embed_key(
                key_id=draft.embed_key_id, provider=draft.embed_provider,
            )
        if draft.rerank_enabled:
            if draft.rerank_key_id is None or not draft.rerank_provider:
                raise CapabilityMismatch(
                    "rerank_enabled=true requires rerank_key_id + rerank_provider"
                )
            await self._validate_rerank_key(
                key_id=draft.rerank_key_id, provider=draft.rerank_provider,
            )

        cfg = await self._configs.create(
            project_id=project_id,
            name=draft.name,
            chunk_strategy=draft.chunk_strategy,
            chunk_params=draft.chunk_params,
            embed_key_id=draft.embed_key_id,
            embed_provider=draft.embed_provider,
            embed_model=draft.embed_model,
            rerank_enabled=draft.rerank_enabled,
            rerank_key_id=draft.rerank_key_id,
            rerank_provider=draft.rerank_provider,
            rerank_model=draft.rerank_model,
            top_k=draft.top_k,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "name": cfg.name,
                    "chunk_strategy": cfg.chunk_strategy.value,
                    "embed_provider": cfg.embed_provider,
                    "embed_model": cfg.embed_model,
                    "rerank_enabled": cfg.rerank_enabled,
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def get(self, config_id: uuid.UUID) -> RagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise RagConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[RagConfig]:
        return await self._configs.list_for_project(project_id)

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self.get(config_id)  # 404 if missing
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=config_id,
                request_id=request_id,
            ),
        )


__all__ = ["RagConfigService"]
