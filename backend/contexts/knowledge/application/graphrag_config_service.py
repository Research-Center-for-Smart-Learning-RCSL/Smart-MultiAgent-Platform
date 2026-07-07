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
from contexts.agents.infrastructure import tables as agents_t
from contexts.keys.infrastructure import tables as keys_t
from contexts.knowledge.domain.errors import (
    GraphRagAgentProjectMismatch,
    GraphRagBuilderKeyGroupProjectMismatch,
    GraphRagConfigNotFound,
)
from contexts.knowledge.domain.graphrag import (
    BuildState,
    GraphRagConfig,
    GraphRagConfigDraft,
)
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
        # Project-scope — confirm the agent lives in this project before we
        # wrap it in a singleton owner group. (The former builder-vs-consumer
        # key-group distinctness check is dropped in Phase 1: ownership is an
        # agent_group, which has no consumer key group to collide with.)
        agent_row = (
            await self._db.execute(
                sa.select(
                    agents_t.agents.c.project_id,
                ).where(
                    sa.and_(
                        agents_t.agents.c.id == draft.agent_id,
                        agents_t.agents.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if agent_row is None:
            raise GraphRagAgentProjectMismatch(str(draft.agent_id))
        if agent_row.project_id != project_id:
            raise GraphRagAgentProjectMismatch(f"agent {draft.agent_id} is not in project {project_id}")

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

        owner_group_id = await self._ensure_singleton_agent_group(
            project_id=project_id, agent_id=draft.agent_id
        )
        cfg = await self._configs.create(
            project_id=project_id,
            agent_id=draft.agent_id,
            owner_agent_group_id=owner_group_id,
            builder_key_group_id=draft.builder_key_group_id,
            trigger_config=draft.trigger_config,
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
                    "agent_id": str(draft.agent_id),
                    "builder_key_group_id": str(draft.builder_key_group_id),
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def _ensure_singleton_agent_group(
        self,
        *,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> uuid.UUID:
        """Return the agent's singleton owner group id, creating it if absent.

        Phase 1 keeps the agent-centric create UX (Q-4): a Concept Map is
        created for an agent, and the service auto-wraps that agent in a
        singleton ``agent_group`` used as the config's owner. The synthetic
        name ``graphrag-owner-{agent_id}`` matches the 0043 backfill, so the
        create path and the migration converge on the same group shape.
        """
        name = f"graphrag-owner-{agent_id}"
        existing = (
            await self._db.execute(
                sa.select(ag.agent_groups.c.id).where(
                    sa.and_(
                        ag.agent_groups.c.project_id == project_id,
                        ag.agent_groups.c.name == name,
                        ag.agent_groups.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if existing is not None:
            return cast(uuid.UUID, existing.id)
        group_id = cast(
            uuid.UUID,
            (
                await self._db.execute(
                    ag.agent_groups.insert()
                    .values(project_id=project_id, name=name)
                    .returning(ag.agent_groups.c.id)
                )
            ).scalar_one(),
        )
        await self._db.execute(
            ag.agent_group_members.insert().values(agent_group_id=group_id, agent_id=agent_id)
        )
        return group_id

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
        if builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id:
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

        await self._configs.update(
            config_id=config_id,
            builder_key_group_id=builder_key_group_id,
            trigger_config=trigger_config,
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
