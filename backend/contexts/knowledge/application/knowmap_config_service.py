"""Knowledge Map config CRUD use-cases (Phase 3, R11.12/R11.13).

Mirrors :class:`RagConfigService` + :class:`GraphRagConfigService`: the config
owns validation (builder key group belongs to the project; a resolvable embedding
key exists; the project embedding dimension is consistent), audit emission,
soft-delete with child-document cascade, and the pinned-key embedder construction
shared by the ingest + build paths.

Unlike a Concept Map it has no discriminated owner and no temporality; unlike
file-RAG it resolves its embedding key from a builder Key Group (SEC-H3 carried
path) rather than a single ``embed_key_id`` column.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.infrastructure import tables as keys_t
from contexts.knowledge.application.embed_resolution import (
    resolve_embed_key,
    resolve_pinned_embed_key,
)
from contexts.knowledge.domain.errors import (
    KnowmapBuilderKeyGroupProjectMismatch,
    KnowmapConfigNotFound,
    KnowmapEmbedDimensionConflict,
    KnowmapNoEmbeddingKey,
)
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapConfigDraft, KnowmapDocument
from contexts.knowledge.domain.models import embed_dimension
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)


class KnowmapConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = KnowmapConfigRepository(db)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: KnowmapConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapConfig:
        await self._assert_builder_group_in_project(draft.builder_key_group_id, project_id)
        # The builder group MUST resolve an embedding key: the corpus is chunked
        # and graph entities are embedded into knowmap_{project_id} at build, so a
        # config that can never embed is invalid (unlike graphrag's nullable pin).
        pin = await self._resolve_group_pin(draft.builder_key_group_id)
        if pin is None:
            raise KnowmapNoEmbeddingKey(
                f"builder key group {draft.builder_key_group_id} has no actively-carried embedding key"
            )
        embed_provider, embed_model, embed_dim = pin
        existing_dim = await self._configs.project_pinned_dim(project_id)
        if existing_dim is not None and existing_dim != embed_dim:
            raise KnowmapEmbedDimensionConflict(
                f"project {project_id} is pinned to {existing_dim}-dim knowmap embeddings; "
                f"builder key group {draft.builder_key_group_id} resolves to {embed_dim}-dim"
            )

        cfg = await self._configs.create(
            project_id=project_id,
            name=draft.name,
            builder_key_group_id=draft.builder_key_group_id,
            chunk_strategy=draft.chunk_strategy,
            chunk_params=draft.chunk_params,
            embed_provider=embed_provider,
            embed_model=embed_model,
            embed_dim=embed_dim,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "name": cfg.name,
                    "chunk_strategy": cfg.chunk_strategy.value,
                    "builder_key_group_id": str(draft.builder_key_group_id),
                    "embed_provider": embed_provider,
                    "embed_model": embed_model,
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def get(self, config_id: uuid.UUID) -> KnowmapConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[KnowmapConfig]:
        return await self._configs.list_for_project(project_id)

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        patch: dict[str, Any],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapConfig:
        cfg = await self.get(config_id)
        new_group = patch.get("builder_key_group_id")
        db_values: dict[str, Any] = {}
        for k in ("name", "chunk_params"):
            if k in patch:
                db_values[k] = patch[k]
        if new_group is not None and new_group != cfg.builder_key_group_id:
            await self._assert_builder_group_in_project(new_group, cfg.project_id)
            pin = await self._resolve_group_pin(new_group)
            if pin is None:
                raise KnowmapNoEmbeddingKey(
                    f"builder key group {new_group} has no actively-carried embedding key"
                )
            provider, model, dim = pin
            existing_dim = await self._configs.project_pinned_dim(cfg.project_id, exclude_config_id=config_id)
            if existing_dim is not None and existing_dim != dim:
                raise KnowmapEmbedDimensionConflict(
                    f"project {cfg.project_id} is pinned to {existing_dim}-dim knowmap embeddings; "
                    f"builder key group {new_group} resolves to {dim}-dim"
                )
            db_values["builder_key_group_id"] = new_group
            db_values["embed_provider"] = provider
            db_values["embed_model"] = model
            db_values["embed_dim"] = dim

        updated = await self._configs.update(config_id, db_values)
        if updated is None:
            raise KnowmapConfigNotFound(str(config_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=config_id,
                metadata={"project_id": str(cfg.project_id), "changed_fields": list(db_values.keys())},
                request_id=request_id,
            ),
        )
        return updated

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Sequence[KnowmapDocument]:
        """Soft-delete the config and hard-delete its child documents.

        Returns the deleted documents (carrying ``minio_path``) so the caller can
        purge Qdrant points + MinIO blobs after the DB commit — infra cleanup must
        trail the durable audit row (DOM-4). ``knowmap_chunks`` cascade via FK.
        """
        cfg = await self.get(config_id)
        docs_repo = KnowmapDocumentRepository(self._db)
        page_size = 10_000
        docs: list[KnowmapDocument] = []
        while True:
            batch = list(await docs_repo.list_for_config(config_id, limit=page_size))
            if not batch:
                break
            docs.extend(batch)
            for doc in batch:
                await docs_repo.delete(doc.id)
            if len(batch) < page_size:
                break
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=config_id,
                metadata={"project_id": str(cfg.project_id), "cascaded_documents": len(docs)},
                request_id=request_id,
            ),
        )
        return docs

    # ---- helpers ----------------------------------------------------------

    async def _assert_builder_group_in_project(
        self, builder_key_group_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        row = (
            await self._db.execute(
                sa.select(keys_t.key_groups.c.project_id).where(
                    sa.and_(
                        keys_t.key_groups.c.id == builder_key_group_id,
                        keys_t.key_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if row is None or row.project_id != project_id:
            raise KnowmapBuilderKeyGroupProjectMismatch(
                f"builder_key_group_id {builder_key_group_id} does not belong to project {project_id}"
            )

    async def _resolve_group_pin(self, builder_key_group_id: uuid.UUID) -> tuple[str, str, int] | None:
        resolved = await resolve_embed_key(self._db, builder_key_group_id)
        if resolved is None:
            return None
        provider, model, _key_id = resolved
        return provider, model, embed_dimension(provider, model)


async def build_knowmap_embedder(db: AsyncSession, cfg: KnowmapConfig) -> Any:
    """Construct the pinned-key embedder for a Knowledge Map's ingest + build.

    Resolves ``(provider, model, key_id)`` honouring the config's project pin
    (Phase 2a D2) through the shared carried-key path, then builds a router-backed
    embedder. The BYO key is unwrapped/scrubbed/accounted inside the router; no
    plaintext enters the caller.
    """
    from contexts.keys.infrastructure.adapters import build_router
    from contexts.knowledge.infrastructure.embedders import router_embedder_for

    provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
    return router_embedder_for(router=build_router(db), key_id=key_id, provider=provider, model=model)


__all__ = ["KnowmapConfigService", "build_knowmap_embedder"]
