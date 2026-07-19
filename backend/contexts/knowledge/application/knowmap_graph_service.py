"""Knowledge Map graph read-model for visualization (Phase 3β, R11.24).

Mirror of :mod:`graphrag_graph_service` resolving the config through
``KnowmapConfigRepository`` instead of ``GraphRagConfigRepository`` — the
row-assembly logic both share lives in
``contexts.knowledge.domain.graph_view_assembly``, so this module and its
sibling only differ in config resolution and their own dataclass types.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.errors import KnowmapConfigNotFound
from contexts.knowledge.domain.graph_view_assembly import assemble_graph_view
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
)
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

DEFAULT_GRAPH_LIMIT = 500
MAX_GRAPH_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class KnowmapGraphNode:
    name: str
    degree: int
    build_id: str | None
    type: str


@dataclass(frozen=True, slots=True)
class KnowmapGraphEdge:
    source: str
    relation: str
    target: str
    confidence: float


@dataclass(frozen=True, slots=True)
class KnowmapGraphView:
    config_id: uuid.UUID
    project_id: uuid.UUID
    nodes: tuple[KnowmapGraphNode, ...]
    edges: tuple[KnowmapGraphEdge, ...]
    truncated: bool
    # See GraphView.build_state_blocked -- the build state, not the data, made this empty.
    build_state_blocked: bool = False


class KnowmapGraphService:
    """Read-only assembler for the Knowledge Map graph visualizer."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = KnowmapConfigRepository(db)

    async def get_graph(
        self,
        *,
        config_id: uuid.UUID,
        limit: int = DEFAULT_GRAPH_LIMIT,
    ) -> KnowmapGraphView:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(config_id))

        if cfg.last_build_state in IN_FLIGHT_BUILD_STATES:
            # Mirrors GraphRagGraphService.get_graph and the shared retrieval gate.
            return KnowmapGraphView(
                config_id=config_id,
                project_id=cfg.project_id,
                nodes=(),
                edges=(),
                truncated=False,
                build_state_blocked=True,
            )

        bounded = max(1, min(limit, MAX_GRAPH_LIMIT))

        from app.config.settings import get_settings

        settings = get_settings()
        driver = Neo4jAsyncDriver(
            uri=settings.neo4j.url,
            auth=(settings.neo4j.user, settings.neo4j.password),
        )
        try:
            raw = await driver.fetch_graph(config_id=config_id, limit=bounded)
        finally:
            await driver.close()

        nodes, edges, truncated = assemble_graph_view(
            raw, node_cls=KnowmapGraphNode, edge_cls=KnowmapGraphEdge
        )

        return KnowmapGraphView(
            config_id=config_id,
            project_id=cfg.project_id,
            nodes=nodes,
            edges=edges,
            truncated=truncated,
        )


__all__ = [
    "DEFAULT_GRAPH_LIMIT",
    "MAX_GRAPH_LIMIT",
    "KnowmapGraphEdge",
    "KnowmapGraphNode",
    "KnowmapGraphService",
    "KnowmapGraphView",
]
