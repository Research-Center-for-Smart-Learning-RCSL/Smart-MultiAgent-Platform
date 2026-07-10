"""Knowledge Map graph read-model for visualization (Phase 3β, R11.24).

Standalone mirror of :mod:`graphrag_graph_service` — same bounded node/edge
assembly over the shared, domain-agnostic ``Neo4jAsyncDriver.fetch_graph``,
but resolving the config through ``KnowmapConfigRepository`` instead of
``GraphRagConfigRepository``. Kept as its own module rather than a
generalized service so this task never touches the already-shipped Concept
Map read path (see the task's spec, Q-2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.errors import KnowmapConfigNotFound
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

        nodes: dict[str, KnowmapGraphNode] = {}
        for row in raw.get("nodes") or []:
            name = str(row.get("name") or "")
            if not name:
                continue
            b_raw = row.get("build_id")
            nodes[name] = KnowmapGraphNode(
                name=name,
                degree=int(row.get("degree") or 0),
                build_id=str(b_raw) if b_raw else None,
                type=str(row.get("type") or ""),
            )

        edges: list[KnowmapGraphEdge] = []
        for row in raw.get("edges") or []:
            source = str(row.get("subject") or "")
            target = str(row.get("object") or "")
            if not source or not target:
                continue
            edges.append(
                KnowmapGraphEdge(
                    source=source,
                    relation=str(row.get("relation") or ""),
                    target=target,
                    confidence=float(row.get("confidence") or 0.0),
                )
            )
            # Keep the view self-consistent: an edge endpoint outside the
            # degree-capped node window still needs a node to attach to.
            for endpoint in (source, target):
                if endpoint not in nodes:
                    nodes[endpoint] = KnowmapGraphNode(name=endpoint, degree=0, build_id=None, type="")

        return KnowmapGraphView(
            config_id=config_id,
            project_id=cfg.project_id,
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            truncated=bool(raw.get("truncated")),
        )


__all__ = [
    "DEFAULT_GRAPH_LIMIT",
    "MAX_GRAPH_LIMIT",
    "KnowmapGraphEdge",
    "KnowmapGraphNode",
    "KnowmapGraphService",
    "KnowmapGraphView",
]
