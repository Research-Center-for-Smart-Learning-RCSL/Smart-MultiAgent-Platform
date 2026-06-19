"""RAG config use-cases — CRUD with capability validation (R10.05)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

from contexts.keys.domain.errors import CapabilityMismatch as KeysCapabilityMismatch
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
)
from contexts.keys.infrastructure import tables as keys_t
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from contexts.knowledge.domain.errors import (
    CapabilityMismatch,
    EmbedModelNotWhitelisted,
    RagConfigNotFound,
)
from contexts.knowledge.domain.models import (
    EMBED_MODEL_WHITELIST,
    RagConfig,
    RagConfigDraft,
    RagDocument,
)
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit

_EMBED = CapabilityRequirement(capability=ProviderCapability.EMBEDDING)
_RERANK = CapabilityRequirement(capability=ProviderCapability.RERANK)


class RagConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._keys = ApiKeyRepository(db)

    async def _validate_embed_key(
        self, *, key_id: uuid.UUID, provider: str, project_id: uuid.UUID
    ) -> ApiKey:
        key = await self._keys.get_active(key_id)
        if key is None:
            raise CapabilityMismatch(f"embed_key {key_id} missing or deleted")
        scope_row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(
                    keys_t.key_group_members.join(
                        keys_t.key_groups,
                        keys_t.key_groups.c.id == keys_t.key_group_members.c.group_id,
                    )
                )
                .where(
                    sa.and_(
                        keys_t.key_group_members.c.key_id == key_id,
                        keys_t.key_groups.c.project_id == project_id,
                        keys_t.key_groups.c.deleted_at.is_(None),
                    )
                )
                .limit(1)
            )
        ).first()
        if scope_row is None:
            raise CapabilityMismatch(f"embed_key {key_id} does not belong to project")
        if key.provider.value != provider:
            raise CapabilityMismatch(
                f"embed_key provider {key.provider.value!r} != config provider {provider!r}"
            )
        try:
            assert_capability(ApiKeyProvider(key.provider.value), _EMBED)
        except KeysCapabilityMismatch as exc:
            raise CapabilityMismatch(str(exc)) from exc
        return key

    async def _validate_rerank_key(self, *, key_id: uuid.UUID, provider: str) -> ApiKey:
        key = await self._keys.get_active(key_id)
        if key is None:
            raise CapabilityMismatch(f"rerank_key {key_id} missing or deleted")
        if key.provider.value != provider:
            raise CapabilityMismatch(f"rerank_key provider {key.provider.value!r} != {provider!r}")
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
            raise EmbedModelNotWhitelisted(f"{draft.embed_provider}:{draft.embed_model} not whitelisted")

        if draft.embed_key_id is not None:
            await self._validate_embed_key(
                key_id=draft.embed_key_id,
                provider=draft.embed_provider,
                project_id=project_id,
            )
        if draft.rerank_enabled:
            if draft.rerank_key_id is None or not draft.rerank_provider:
                raise CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")
            await self._validate_rerank_key(
                key_id=draft.rerank_key_id,
                provider=draft.rerank_provider,
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

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        patch: dict[str, Any],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagConfig:
        """Apply a partial update to a RAG config (mutable fields only)."""
        cfg = await self.get(config_id)  # 404 if missing

        # Validate rerank key if being changed or enabled.
        rerank_enabled = patch.get("rerank_enabled", cfg.rerank_enabled)
        if rerank_enabled:
            rerank_key_id = patch.get("rerank_key_id", cfg.rerank_key_id)
            rerank_provider = patch.get("rerank_provider", cfg.rerank_provider)
            if rerank_key_id is None or not rerank_provider:
                raise CapabilityMismatch(
                    "rerank_enabled=true requires rerank_key_id + rerank_provider"
                )
            if "rerank_key_id" in patch or "rerank_provider" in patch:
                await self._validate_rerank_key(
                    key_id=rerank_key_id, provider=rerank_provider
                )

        # Only allow mutable fields.
        _MUTABLE = {
            "name", "top_k", "chunk_params",
            "rerank_enabled", "rerank_key_id", "rerank_provider", "rerank_model",
        }
        db_values: dict[str, Any] = {}
        for k, v in patch.items():
            if k in _MUTABLE:
                db_values[k] = v

        updated = await self._configs.update(config_id, db_values)
        if updated is None:
            raise RagConfigNotFound(str(config_id))

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "changed_fields": list(db_values.keys()),
                },
                request_id=request_id,
            ),
        )
        return updated

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
    ) -> Sequence[RagDocument]:
        """Soft-delete the config and hard-delete its child documents.

        DOM-1: a config delete must cascade. ``rag_documents`` are FK'd to
        ``rag_configs`` but Postgres does not cascade across a *soft* delete,
        so the document rows are hard-deleted here — ``rag_chunks`` follow
        via their own ``ON DELETE CASCADE`` FK. The deleted documents are
        returned (carrying ``minio_path``) so the caller can purge Qdrant
        points + MinIO blobs *after* the DB commit — infra cleanup must
        trail the durable audit row, never precede it (DOM-4).
        """
        cfg = await self.get(config_id)  # 404 if missing
        docs_repo = RagDocumentRepository(self._db)
        # Drain all child documents in batches — an unbounded single fetch
        # would OOM on very large configs, and the previous limit=10_000 cap
        # silently left orphan documents behind.
        _BATCH = 10_000
        docs: list[RagDocument] = []
        while True:
            batch = list(await docs_repo.list_for_config(config_id, limit=_BATCH))
            if not batch:
                break
            docs.extend(batch)
            for doc in batch:
                await docs_repo.delete(doc.id)
            if len(batch) < _BATCH:
                break
        if len(docs) >= 20_000:
            _log.warning(
                "soft_delete cascaded %d documents for config %s — consider async cleanup",
                len(docs),
                config_id,
            )
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "cascaded_documents": len(docs),
                },
                request_id=request_id,
            ),
        )
        return docs


__all__ = ["RagConfigService"]
