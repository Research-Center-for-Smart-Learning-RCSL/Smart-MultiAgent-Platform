"""GraphRAG config CRUD use-cases (E.7 / R11.01, R11.05, R11a.02).

The service owns validation (builder key-group belongs to the config's
project, 1:1 with agent guaranteed by the DB UNIQUE), audit emission,
soft-delete, and external store cascade (Neo4j + Qdrant teardown).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.infrastructure import tables as ag
from contexts.conversation.infrastructure import tables as conv_t
from contexts.keys.infrastructure import tables as keys_t
from contexts.knowledge.application.embed_resolution import resolve_embed_key
from contexts.knowledge.domain.errors import (
    GraphRagBuilderKeyGroupProjectMismatch,
    GraphRagConfigNotFound,
    GraphRagEmbedDimensionConflict,
    GraphRagOwnerProjectMismatch,
)
from contexts.knowledge.domain.graphrag import (
    BuildState,
    GraphRagConfig,
    GraphRagConfigDraft,
)
from contexts.knowledge.domain.models import embed_dimension
from contexts.knowledge.infrastructure import graphrag_tables as gt
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from shared_kernel import audit

if TYPE_CHECKING:
    from contexts.knowledge.application.graphrag_ports import Neo4jDriver
    from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore

_log = logging.getLogger(__name__)


class GraphRagConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = GraphRagConfigRepository(db)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: GraphRagConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        # Phase 2b WS2: owner-centric. The discriminated owner must already exist
        # and live in this project (the former agent-centric auto-singleton wrap
        # is retired; an agent_group owner is created + populated via the
        # member-CRUD surface first). _assert_owner_in_project handles all three
        # owner kinds.
        await self._assert_owner_in_project(
            owner_kind=draft.owner_kind, owner_id=draft.owner_id, project_id=project_id
        )

        builder_group = (
            await self._db.execute(
                sa.select(keys_t.key_groups.c.project_id).where(
                    sa.and_(
                        keys_t.key_groups.c.id == draft.builder_key_group_id,
                        keys_t.key_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if builder_group is None or builder_group.project_id != project_id:
            raise GraphRagBuilderKeyGroupProjectMismatch(
                f"builder_key_group_id {draft.builder_key_group_id} "
                f"does not belong to project {project_id}"
            )

        # Phase 2a D2: derive + enforce the project embedding pin from the builder
        # key group. If the group has no embedding key yet the pin is left null and
        # the config self-pins on its first successful build.
        pin = await self._enforce_and_resolve_pin(project_id, draft.builder_key_group_id)
        embed_provider, embed_model, embed_dim = pin if pin is not None else (None, None, None)

        cfg = await self._configs.create(
            project_id=project_id,
            owner_kind=draft.owner_kind,
            owner_id=draft.owner_id,
            builder_key_group_id=draft.builder_key_group_id,
            trigger_config=draft.trigger_config,
            embed_provider=embed_provider,
            embed_model=embed_model,
            embed_dim=embed_dim,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "owner_kind": draft.owner_kind,
                    "owner_id": str(draft.owner_id),
                    "builder_key_group_id": str(draft.builder_key_group_id),
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def _resolve_group_pin(
        self,
        builder_key_group_id: uuid.UUID,
    ) -> tuple[str, str, int] | None:
        """Resolve the builder key group to ``(provider, model, dim)`` (D2).

        Returns ``None`` when the group has no actively-carried embedding key, so
        the caller leaves the pin null and the config self-pins on first build.
        """
        resolved = await resolve_embed_key(self._db, builder_key_group_id)
        if resolved is None:
            return None
        provider, model, _key_id = resolved
        return provider, model, embed_dimension(provider, model)

    async def _project_pinned_dim(
        self,
        project_id: uuid.UUID,
        *,
        exclude_config_id: uuid.UUID | None = None,
    ) -> int | None:
        """The project's already-pinned embedding dimension, if any (D2).

        Read from a sibling config's persisted ``embed_dim`` (Postgres-only, so
        config CRUD never depends on Qdrant availability). The build-time
        collection-dimension guard (D7) is the backstop for the transitional case
        where a project has a built collection but no yet-pinned sibling.
        """
        stmt = sa.select(gt.graphrag_configs.c.embed_dim).where(
            sa.and_(
                gt.graphrag_configs.c.project_id == project_id,
                gt.graphrag_configs.c.embed_dim.isnot(None),
                gt.graphrag_configs.c.deleted_at.is_(None),
            )
        )
        if exclude_config_id is not None:
            stmt = stmt.where(gt.graphrag_configs.c.id != exclude_config_id)
        row = (await self._db.execute(stmt.limit(1))).first()
        return int(row.embed_dim) if row is not None else None

    async def _enforce_and_resolve_pin(
        self,
        project_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        *,
        exclude_config_id: uuid.UUID | None = None,
    ) -> tuple[str, str, int] | None:
        """Resolve the group pin and reject it if it conflicts with the project.

        Returns the resolved ``(provider, model, dim)`` to persist, or ``None``
        when the group yields no embedding key. Raises
        :class:`GraphRagEmbedDimensionConflict` (422) when the resolved dimension
        differs from the project's existing pin (R11.18).
        """
        pin = await self._resolve_group_pin(builder_key_group_id)
        if pin is None:
            return None
        _, _, dim = pin
        existing = await self._project_pinned_dim(project_id, exclude_config_id=exclude_config_id)
        if existing is not None and existing != dim:
            raise GraphRagEmbedDimensionConflict(
                f"project {project_id} is pinned to {existing}-dim embeddings; "
                f"builder key group {builder_key_group_id} resolves to {dim}-dim"
            )
        return pin

    async def _assert_owner_in_project(
        self,
        *,
        owner_kind: str,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        """Reject an owner entity not in the config's project (D6, AC-9).

        Dispatch per ``owner_kind``: ``agent_group`` and ``workspace`` carry
        ``project_id`` directly; ``chatroom`` reaches it via its workspace
        (2-hop). Raises :class:`GraphRagOwnerProjectMismatch` (422) on a project
        mismatch or a missing/deleted owner. Implemented for every kind now even
        though Phase 1 create only exercises ``agent_group`` — Phase 2b adds the
        chatroom/workspace owner surfaces and this guard is already in place.
        """
        if owner_kind == "agent_group":
            stmt = sa.select(ag.agent_groups.c.project_id).where(
                sa.and_(
                    ag.agent_groups.c.id == owner_id,
                    ag.agent_groups.c.deleted_at.is_(None),
                )
            )
        elif owner_kind == "workspace":
            stmt = sa.select(conv_t.workspaces.c.project_id).where(
                sa.and_(
                    conv_t.workspaces.c.id == owner_id,
                    conv_t.workspaces.c.deleted_at.is_(None),
                )
            )
        elif owner_kind == "chatroom":
            stmt = (
                sa.select(conv_t.workspaces.c.project_id)
                .select_from(
                    conv_t.chatrooms.join(
                        conv_t.workspaces,
                        conv_t.workspaces.c.id == conv_t.chatrooms.c.workspace_id,
                    )
                )
                .where(
                    sa.and_(
                        conv_t.chatrooms.c.id == owner_id,
                        conv_t.workspaces.c.deleted_at.is_(None),
                    )
                )
            )
        else:
            raise GraphRagOwnerProjectMismatch(f"unknown owner_kind {owner_kind!r}")

        row = (await self._db.execute(stmt)).first()
        if row is None or row.project_id != project_id:
            raise GraphRagOwnerProjectMismatch(f"{owner_kind} {owner_id} is not in project {project_id}")

    async def _config_owner(self, config_id: uuid.UUID) -> tuple[str, uuid.UUID]:
        """Return the config's discriminated owner ``(owner_kind, owner_id)``."""
        row = (
            await self._db.execute(
                sa.select(
                    gt.graphrag_configs.c.owner_kind,
                    gt.graphrag_configs.c.owner_chatroom_id,
                    gt.graphrag_configs.c.owner_agent_group_id,
                    gt.graphrag_configs.c.owner_workspace_id,
                ).where(gt.graphrag_configs.c.id == config_id)
            )
        ).one()
        owner_id = {
            "chatroom": row.owner_chatroom_id,
            "agent_group": row.owner_agent_group_id,
            "workspace": row.owner_workspace_id,
        }[row.owner_kind]
        return cast(str, row.owner_kind), cast(uuid.UUID, owner_id)

    async def get(self, config_id: uuid.UUID) -> GraphRagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        return await self._configs.list_for_project(project_id)

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        cfg = await self.get(config_id)
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={"project_id": str(cfg.project_id)},
                request_id=request_id,
            ),
        )
        return cfg

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID | None,
        trigger_config: dict[str, Any] | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        """R11.05 partial update — edit trigger config or builder key group.

        Re-checks that a swapped builder key group still lives in the config's
        project. The former builder-vs-agent-consumer distinctness check is
        dropped in Phase 1: ownership is an agent_group, not a single agent, so
        there is no per-agent consumer key group for the builder to collide with.
        """
        cfg = await self.get(config_id)
        # D6: owner is immutable post-create, but re-assert defensively that it
        # still lives in the config's project before any mutation.
        owner_kind, owner_id = await self._config_owner(config_id)
        await self._assert_owner_in_project(
            owner_kind=owner_kind, owner_id=owner_id, project_id=cfg.project_id
        )
        group_changed = builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id
        new_pin: tuple[str, str, int] | None = None
        if group_changed:
            builder_group = (
                await self._db.execute(
                    sa.select(keys_t.key_groups.c.project_id).where(
                        sa.and_(
                            keys_t.key_groups.c.id == builder_key_group_id,
                            keys_t.key_groups.c.deleted_at.is_(None),
                        )
                    )
                )
            ).first()
            if builder_group is None or builder_group.project_id != cfg.project_id:
                raise GraphRagBuilderKeyGroupProjectMismatch(
                    f"builder_key_group_id {builder_key_group_id} "
                    f"does not belong to project {cfg.project_id}"
                )
            # Phase 2a D2: a swapped builder group must still resolve to the
            # project's pinned dimension; re-derive and persist the new pin.
            assert builder_key_group_id is not None
            new_pin = await self._enforce_and_resolve_pin(
                cfg.project_id, builder_key_group_id, exclude_config_id=config_id
            )

        await self._configs.update(
            config_id=config_id,
            builder_key_group_id=builder_key_group_id,
            trigger_config=trigger_config,
        )
        if group_changed and new_pin is not None:
            await self._configs.set_embed_pin(
                config_id=config_id,
                provider=new_pin[0],
                model=new_pin[1],
                dim=new_pin[2],
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "builder_key_group_id_changed": (
                        builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id
                    ),
                    "trigger_config_changed": trigger_config is not None,
                },
                request_id=request_id,
            ),
        )
        refreshed = await self._configs.get(config_id)
        assert refreshed is not None
        return refreshed

    async def admin_reset(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        """R11a.02 — force state back to `idle`, audit unconditionally."""
        cfg = await self.get(config_id)
        prev = cfg.last_build_state
        await self._configs.set_state(
            config_id=config_id,
            state=BuildState.IDLE,
            error=None,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.graphrag_reset",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "previous_state": prev.value,
                    "project_id": str(cfg.project_id),
                },
                request_id=request_id,
            ),
        )
        refreshed = await self._configs.get(config_id)
        assert refreshed is not None
        return refreshed

    async def status(
        self,
        config_id: uuid.UUID,
    ) -> dict[str, Any]:
        cfg = await self.get(config_id)
        return {
            "id": str(cfg.id),
            "state": cfg.last_build_state.value,
            "last_build_at": (cfg.last_build_at.isoformat() if cfg.last_build_at else None),
            "last_build_error": cfg.last_build_error,
        }

    # ---- infrastructure cascade -------------------------------------------

    @staticmethod
    async def cascade_external_stores(
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict[str, bool]:
        """Best-effort removal of Neo4j subgraph + Qdrant entity vectors.

        DOM-4: must be called only *after* the soft-delete + audit row have
        been committed. Builds its own short-lived clients (the request path
        owns no long-lived ones) and delegates the actual teardown to
        :func:`purge_config_external_stores`, which the reconciler sweep also
        uses with its own injected clients. Returns a summary dict for the
        follow-up audit row.
        """
        from app.config.settings import get_settings

        settings = get_settings()

        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        neo4j_conf = getattr(settings, "neo4j", None)
        driver = (
            Neo4jAsyncDriver(uri=neo4j_conf.url, auth=(neo4j_conf.user, neo4j_conf.password))
            if neo4j_conf is not None
            else None
        )

        from qdrant_client import AsyncQdrantClient

        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore

        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        try:
            return await purge_config_external_stores(
                config_id=config_id,
                project_id=project_id,
                neo4j=driver,
                vectors=GraphRagVectorStore(qclient),
            )
        finally:
            if driver is not None:
                await driver.close()
            await qclient.close()


async def purge_config_external_stores(
    *,
    config_id: uuid.UUID,
    project_id: uuid.UUID | None,
    neo4j: Neo4jDriver | None,
    vectors: GraphRagVectorStore | None,
) -> dict[str, bool]:
    """Delete a config's Neo4j subgraph (section 22.8) + Qdrant vectors (DOM-2).

    The single teardown both the request-path cascade and the reconciler orphan
    sweep share, using caller-owned clients. Each store is best-effort and
    isolated: a failure on one is reported in the summary dict without aborting
    the other. ``project_id`` may be ``None`` for a legacy orphan whose nodes
    predate the self-describing property; the Qdrant collection is
    project-scoped, so that case is reported ``qdrant_purged=False`` (FU-B).
    """
    neo4j_purged = True
    qdrant_purged = project_id is not None
    if neo4j is not None:
        try:
            await neo4j.delete_all(config_id=config_id)
        except Exception:
            neo4j_purged = False
            _log.exception("graphrag delete: neo4j cascade failed for config %s", config_id)
    if project_id is not None and vectors is not None:
        try:
            await vectors.delete_by_config(project_id=project_id, config_id=config_id)
        except Exception:
            qdrant_purged = False
            _log.exception("graphrag delete: qdrant cascade failed for config %s", config_id)
    return {"neo4j_purged": neo4j_purged, "qdrant_purged": qdrant_purged}


__all__ = ["GraphRagConfigService", "purge_config_external_stores"]
