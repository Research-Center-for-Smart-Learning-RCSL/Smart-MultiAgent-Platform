"""Thin Qdrant surface dedicated to GraphRAG entity collections (E.7/E.8).

Kept as a *separate* lightweight class rather than extending
:class:`contexts.knowledge.infrastructure.qdrant_store.QdrantStore` so the
RAG wrapper's payload shape (`{doc_id, chunk_idx, agent_ids}`) stays
distinct from the GraphRAG entity payload (`{entity, build_id,
description}`). Each project-scoped GraphRAG collection is named
``graphrag_{project_id}`` per §21.4 / R11.03.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

__all__ = ["GraphRagEntityHit", "GraphRagVectorStore", "graphrag_collection_name"]


def graphrag_collection_name(project_id: uuid.UUID) -> str:
    """Per-project GraphRAG collection name (§21.4)."""
    return f"graphrag_{str(project_id).replace('-', '_')}"


@dataclass(frozen=True, slots=True)
class GraphRagEntityHit:
    point_id: uuid.UUID
    score: float
    entity: str
    description: str
    build_id: uuid.UUID | None


class GraphRagVectorStore:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_graphrag_collection(
        self,
        project_id: uuid.UUID,
        *,
        vector_size: int,
        distance: Distance = Distance.COSINE,
    ) -> None:
        name = graphrag_collection_name(project_id)
        if await self._client.collection_exists(name):
            return
        await self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=distance),
        )

    async def upsert_entities(
        self,
        *,
        project_id: uuid.UUID,
        build_id: uuid.UUID,
        points: Iterable[tuple[uuid.UUID, list[float], str, str]],
    ) -> None:
        """Upsert (point_id, vector, entity, description) tuples tagged with build_id."""
        structs = [
            PointStruct(
                id=str(pid),
                vector=vec,
                payload={
                    "entity": entity,
                    "description": description,
                    "build_id": str(build_id),
                },
            )
            for (pid, vec, entity, description) in points
        ]
        if not structs:
            return
        await self._client.upsert(
            collection_name=graphrag_collection_name(project_id),
            points=structs,
            wait=True,
        )

    async def search_entities(
        self,
        *,
        project_id: uuid.UUID,
        query_vector: list[float],
        top_k: int,
        build_id: uuid.UUID | None = None,
    ) -> list[GraphRagEntityHit]:
        must: list[FieldCondition] = []
        if build_id is not None:
            must.append(
                FieldCondition(
                    key="build_id", match=MatchValue(value=str(build_id)),
                )
            )
        qfilter = Filter(must=must) if must else None
        results = await self._client.search(
            collection_name=graphrag_collection_name(project_id),
            query_vector=query_vector,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        out: list[GraphRagEntityHit] = []
        for r in results:
            pid = r.id
            if not isinstance(pid, uuid.UUID):
                try:
                    pid = uuid.UUID(str(pid))
                except ValueError:
                    continue
            payload: dict[str, Any] = dict(r.payload or {})
            b_raw = payload.get("build_id")
            try:
                b_uuid = uuid.UUID(str(b_raw)) if b_raw else None
            except ValueError:
                b_uuid = None
            out.append(
                GraphRagEntityHit(
                    point_id=pid,
                    score=float(r.score or 0.0),
                    entity=str(payload.get("entity") or ""),
                    description=str(payload.get("description") or ""),
                    build_id=b_uuid,
                )
            )
        return out

    async def delete_by_build(
        self, *, project_id: uuid.UUID, build_id: uuid.UUID,
    ) -> None:
        await self._client.delete(
            collection_name=graphrag_collection_name(project_id),
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="build_id",
                        match=MatchValue(value=str(build_id)),
                    )
                ]
            ),
            wait=True,
        )

    async def delete_collection(self, project_id: uuid.UUID) -> None:
        name = graphrag_collection_name(project_id)
        if await self._client.collection_exists(name):
            await self._client.delete_collection(collection_name=name)
