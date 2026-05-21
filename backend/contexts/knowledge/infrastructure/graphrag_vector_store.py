"""Thin Qdrant surface dedicated to GraphRAG entity collections (E.7/E.8).

Kept as a *separate* lightweight class rather than extending
:class:`contexts.knowledge.infrastructure.qdrant_store.QdrantStore` so the
RAG wrapper's payload shape (`{doc_id, chunk_idx, agent_ids}`) stays
distinct from the GraphRAG entity payload (`{config_id, entity,
description, build_id}`). Each project-scoped GraphRAG collection is named
``graphrag_{project_id}`` per §21.4 / R11.03.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

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
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        points: Iterable[tuple[uuid.UUID, list[float], str, str]],
    ) -> None:
        """Upsert (point_id, vector, entity, description) tuples.

        Each point is tagged with both ``config_id`` and ``build_id``. The
        ``config_id`` tag is what scopes search + delete to a single config
        even though every config in a project shares the one
        ``graphrag_{project_id}`` collection — without it (DOM-2) deleting a
        config could not target its points, and retrieval bled across
        sibling configs.
        """
        structs = [
            PointStruct(
                id=str(pid),
                vector=vec,
                payload={
                    "config_id": str(config_id),
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
        config_id: uuid.UUID | None = None,
        build_id: uuid.UUID | None = None,
    ) -> list[GraphRagEntityHit]:
        must: list[FieldCondition] = []
        if config_id is not None:
            must.append(
                FieldCondition(
                    key="config_id",
                    match=MatchValue(value=str(config_id)),
                )
            )
        if build_id is not None:
            must.append(
                FieldCondition(
                    key="build_id",
                    match=MatchValue(value=str(build_id)),
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
            if not isinstance(pid, uuid.UUID):  # type: ignore[unreachable]
                try:
                    pid = uuid.UUID(str(pid))  # type: ignore[assignment]
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
                    point_id=pid,  # type: ignore[arg-type]
                    score=float(r.score or 0.0),
                    entity=str(payload.get("entity") or ""),
                    description=str(payload.get("description") or ""),
                    build_id=b_uuid,
                )
            )
        return out

    async def delete_by_build(
        self,
        *,
        project_id: uuid.UUID,
        build_id: uuid.UUID,
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

    async def delete_superseded_entities(
        self,
        *,
        project_id: uuid.UUID,
        config_id: uuid.UUID,
        keep_build_id: uuid.UUID,
        entities: Sequence[str],
    ) -> None:
        """Delete prior-build points for entities the live build re-embedded (DOM-8).

        The ``graphrag_{project_id}`` collection accumulates across delta
        builds: each build upserts only the entities in its own delta under a
        fresh ``build_id``, so a config's full entity set is spread over many
        builds. A blanket "delete every build but the latest" would therefore
        destroy live entities that no later delta happened to re-touch.
        Instead, when build ``keep_build_id`` re-embeds a set of entity
        *names*, this removes only the older copies of *those* names — every
        other entity's points are left intact. The filter keeps points that
        are in the live build (``must_not`` build_id) or whose entity name is
        not in this batch (the ``should`` clause).

        No-ops if ``entities`` is empty or the collection does not exist.
        Stale duplicates left by a reconciler-recovered build (which has no
        entity list to hand) are cleared the next time their entity is
        re-embedded.
        """
        unique = list(dict.fromkeys(entities))
        if not unique:
            return
        name = graphrag_collection_name(project_id)
        if not await self._client.collection_exists(name):
            return
        await self._client.delete(
            collection_name=name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="config_id",
                        match=MatchValue(value=str(config_id)),
                    )
                ],
                must_not=[
                    FieldCondition(
                        key="build_id",
                        match=MatchValue(value=str(keep_build_id)),
                    )
                ],
                should=[
                    FieldCondition(
                        key="entity",
                        match=MatchValue(value=entity_name),
                    )
                    for entity_name in unique
                ],
            ),
            wait=True,
        )

    async def delete_by_config(
        self,
        *,
        project_id: uuid.UUID,
        config_id: uuid.UUID,
    ) -> None:
        """Delete every entity point belonging to ``config_id`` (DOM-2 cascade).

        The ``graphrag_{project_id}`` collection is shared by all GraphRAG
        configs in the project, so a config delete must filter on the
        ``config_id`` payload tag — dropping the whole collection would take
        sibling configs' vectors down with it. No-ops if the collection does
        not exist. Points written before ``config_id`` tagging existed carry
        no tag and are not matched here — a rebuild re-tags them.
        """
        name = graphrag_collection_name(project_id)
        if not await self._client.collection_exists(name):
            return
        await self._client.delete(
            collection_name=name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="config_id",
                        match=MatchValue(value=str(config_id)),
                    )
                ]
            ),
            wait=True,
        )

    async def delete_collection(self, project_id: uuid.UUID) -> None:
        name = graphrag_collection_name(project_id)
        if await self._client.collection_exists(name):
            await self._client.delete_collection(collection_name=name)
