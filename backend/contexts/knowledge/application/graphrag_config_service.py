"""GraphRAG config CRUD use-cases (E.7 / R11.01, R11.05, R11a.02).

The service owns validation (builder key-group belongs to the config's
project, 1:1 with agent guaranteed by the DB UNIQUE), audit emission,
soft-delete, and external store cascade (Neo4j + Qdrant teardown).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

from contexts.agents.infrastructure import tables as agents_t
from contexts.knowledge.domain.errors import (
    GraphRagAgentProjectMismatch,
    GraphRagBuilderKeyGroupConflict,
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
        # R11.01 + project-scope — before committing, confirm the agent
        # lives in this project and the builder key group is distinct from
        # the agent's consumer key group. Without this, a user could point
        # the builder at the agent's own keys (defeating the billing
        # separation intent) or attach a config to an agent in a sibling
        # project.
        agent_row = (
            await self._db.execute(
                sa.select(
                    agents_t.agents.c.project_id,
                    agents_t.agents.c.key_group_id,
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
        if agent_row.key_group_id == draft.builder_key_group_id:
            raise GraphRagBuilderKeyGroupConflict(
                f"builder_key_group_id must differ from agent's key_group_id " f"({agent_row.key_group_id})"
            )

        cfg = await self._configs.create(
            project_id=project_id,
            agent_id=draft.agent_id,
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

        Re-runs the same builder/agent invariant we enforce at create time so
        a user can't pivot the builder onto the agent's own consumer keys.
        """
        cfg = await self.get(config_id)
        if builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id:
            agent_row = (
                await self._db.execute(
                    sa.select(agents_t.agents.c.key_group_id).where(
                        agents_t.agents.c.id == cfg.agent_id,
                    )
                )
            ).first()
            if agent_row is not None and agent_row.key_group_id == builder_key_group_id:
                raise GraphRagBuilderKeyGroupConflict(
                    "builder_key_group_id must differ from agent's key_group_id"
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
        been committed. Returns a summary dict for the follow-up audit row.
        """
        from app.config.settings import get_settings

        settings = get_settings()
        neo4j_purged = True
        qdrant_purged = True

        # Cascade the Neo4j subgraph (section 22.8).
        from contexts.knowledge.infrastructure.neo4j_driver import (
            Neo4jAsyncDriver,
        )

        neo4j_conf = getattr(settings, "neo4j", None)
        if neo4j_conf is not None:
            driver = Neo4jAsyncDriver(
                uri=neo4j_conf.url,
                auth=(neo4j_conf.user, neo4j_conf.password),
            )
            try:
                await driver.delete_all(config_id=config_id)
            except Exception:
                neo4j_purged = False
                _log.exception(
                    "graphrag delete: neo4j cascade failed for config %s",
                    config_id,
                )
            finally:
                await driver.close()

        # DOM-2: delete this config's entity vectors from the shared Qdrant
        # collection, scoped by the ``config_id`` payload tag.
        try:
            from qdrant_client import AsyncQdrantClient

            from contexts.knowledge.infrastructure.graphrag_vector_store import (
                GraphRagVectorStore,
            )

            qclient = AsyncQdrantClient(
                url=settings.qdrant.url,
                api_key=settings.qdrant.api_key or None,
            )
            try:
                await GraphRagVectorStore(qclient).delete_by_config(
                    project_id=project_id,
                    config_id=config_id,
                )
            finally:
                await qclient.close()
        except Exception:
            qdrant_purged = False
            _log.exception(
                "graphrag delete: qdrant cascade failed for config %s",
                config_id,
            )

        return {
            "neo4j_purged": neo4j_purged,
            "qdrant_purged": qdrant_purged,
        }


__all__ = ["GraphRagConfigService"]
