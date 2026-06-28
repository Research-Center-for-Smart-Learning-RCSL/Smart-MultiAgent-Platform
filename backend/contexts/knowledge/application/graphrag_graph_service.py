"""GraphRAG graph read-model for visualization (viz P0).

Assembles a bounded node/edge view of a config's Neo4j subgraph for the
frontend graph viewer. Project-scope authorization stays at the API layer
(membership on ``cfg.project_id``); this service only loads the config to
confirm it exists and to resolve the driver, then returns plain domain
dataclasses.

The driver caps both nodes (by degree) and edges (by confidence) so a mature
graph can never stream tens of thousands of rows to a browser. Any edge
endpoint that falls outside the capped node set is added here as a minimal
node, so the returned view is always self-consistent (every edge has both
endpoints present).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.errors import GraphRagConfigNotFound
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

DEFAULT_GRAPH_LIMIT = 500
MAX_GRAPH_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class GraphNode:
    name: str
    degree: int
    build_id: str | None
    type: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    relation: str
    target: str
    confidence: float


@dataclass(frozen=True, slots=True)
class GraphView:
    config_id: uuid.UUID
    project_id: uuid.UUID
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    truncated: bool


class GraphRagGraphService:
    """Read-only assembler for the knowledge-graph visualizer."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = GraphRagConfigRepository(db)

    async def get_graph(
        self,
        *,
        config_id: uuid.UUID,
        limit: int = DEFAULT_GRAPH_LIMIT,
    ) -> GraphView:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagConfigNotFound(str(config_id))

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

        nodes: dict[str, GraphNode] = {}
        for row in raw.get("nodes") or []:
            name = str(row.get("name") or "")
            if not name:
                continue
            b_raw = row.get("build_id")
            nodes[name] = GraphNode(
                name=name,
                degree=int(row.get("degree") or 0),
                build_id=str(b_raw) if b_raw else None,
                type=str(row.get("type") or ""),
            )

        edges: list[GraphEdge] = []
        for row in raw.get("edges") or []:
            source = str(row.get("subject") or "")
            target = str(row.get("object") or "")
            if not source or not target:
                continue
            edges.append(
                GraphEdge(
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
                    nodes[endpoint] = GraphNode(name=endpoint, degree=0, build_id=None, type="")

        return GraphView(
            config_id=config_id,
            project_id=cfg.project_id,
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            truncated=bool(raw.get("truncated")),
        )


__all__ = [
    "DEFAULT_GRAPH_LIMIT",
    "MAX_GRAPH_LIMIT",
    "GraphEdge",
    "GraphNode",
    "GraphRagGraphService",
    "GraphView",
]
