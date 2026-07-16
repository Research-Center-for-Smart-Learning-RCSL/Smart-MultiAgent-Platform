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

from contexts.knowledge.domain.errors import GraphRagCollectionDimensionMismatch

__all__ = ["GraphRagEntityHit", "GraphRagVectorStore", "graphrag_collection_name"]

# Page size for payload-only scroll during orphan enumeration (F-8). Points carry
# tiny payloads and no vectors here, so a large page keeps the round-trip count low.
_SCROLL_PAGE = 256


def _coerce_uuid(value: object) -> uuid.UUID | None:
    """Parse a payload/collection value into a UUID, or ``None`` if unparseable."""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def graphrag_collection_name(project_id: uuid.UUID, *, prefix: str = "graphrag") -> str:
    """Per-project graph collection name (§21.4).

    ``prefix`` lets a second graph subsystem (e.g. a Knowledge Map using
    ``knowmap_{project_id}``) reuse the same store without colliding with the
    Concept Map's ``graphrag_{project_id}`` collection.
    """
    return f"{prefix}_{str(project_id).replace('-', '_')}"


@dataclass(frozen=True, slots=True)
class GraphRagEntityHit:
    point_id: uuid.UUID
    score: float
    entity: str
    description: str
    build_id: uuid.UUID | None


class GraphRagVectorStore:
    def __init__(self, client: AsyncQdrantClient, *, prefix: str = "graphrag") -> None:
        self._client = client
        self._prefix = prefix

    def _name(self, project_id: uuid.UUID) -> str:
        return graphrag_collection_name(project_id, prefix=self._prefix)

    def _project_id_from_name(self, name: str) -> uuid.UUID | None:
        """Recover the project id from a ``{prefix}_{project_id}`` collection name.

        ``graphrag_collection_name`` writes ``project_id`` with dashes replaced by
        underscores; reverse that to parse it back. Returns ``None`` for a name that
        is not one of this store's collections (a different prefix or unparseable
        tail), so enumeration silently skips foreign collections."""
        marker = f"{self._prefix}_"
        if not name.startswith(marker):
            return None
        return _coerce_uuid(name[len(marker) :].replace("_", "-"))

    async def _scroll_config_ids(self, name: str) -> set[uuid.UUID]:
        """Collect distinct ``config_id`` payloads from a known-existing collection.

        Payload-only (``config_id`` field, no vectors), paged to completion. The
        caller has already established the collection exists — this does not re-check."""
        out: set[uuid.UUID] = set()
        offset: Any = None
        while True:
            records, offset = await self._client.scroll(
                collection_name=name,
                with_payload=["config_id"],
                with_vectors=False,
                limit=_SCROLL_PAGE,
                offset=offset,
            )
            for rec in records:
                cid = _coerce_uuid((rec.payload or {}).get("config_id"))
                if cid is not None:
                    out.add(cid)
            if offset is None:
                break
        return out

    async def list_config_ids(self, project_id: uuid.UUID) -> set[uuid.UUID]:
        """Distinct ``config_id``s present in this project's collection (F-8).

        The Qdrant-side discovery index for the reconciler orphan sweep: a config
        whose Neo4j subgraph was deleted but whose Qdrant points survived a partial
        teardown is invisible to the Neo4j enumeration, so the sweep also enumerates
        here. Returns an empty set if the collection does not exist."""
        name = self._name(project_id)
        if not await self._client.collection_exists(name):
            return set()
        return await self._scroll_config_ids(name)

    async def list_all_config_ids(self) -> set[tuple[uuid.UUID, uuid.UUID]]:
        """``(config_id, project_id)`` across every ``{prefix}_*`` collection (F-8).

        The cross-project variant the orphan sweep unions with the Neo4j-sourced
        candidates. Enumerates only collections carrying this store's prefix (each
        registered consumer's store covers its own prefix — ``graphrag_*`` /
        ``knowmap_*``), so the two consumers together cover both products. Scrolls each
        collection by the exact name ``get_collections`` returned — it already proved
        the collection exists, so no per-collection existence re-check or name
        round-trip through ``project_id``."""
        out: set[tuple[uuid.UUID, uuid.UUID]] = set()
        collections = await self._client.get_collections()
        for coll in collections.collections:
            project_id = self._project_id_from_name(coll.name)
            if project_id is None:
                continue
            for config_id in await self._scroll_config_ids(coll.name):
                out.add((config_id, project_id))
        return out

    async def ensure_graphrag_collection(
        self,
        project_id: uuid.UUID,
        *,
        vector_size: int,
        distance: Distance = Distance.COSINE,
    ) -> None:
        """Idempotently ensure the project collection exists at ``vector_size`` (D7).

        If the collection already exists its dimension MUST match — otherwise the
        build would upsert wrong-dimension vectors into a fixed-size collection
        (the build-time backstop to D2). Create tolerates a concurrent create
        (another build won the race) by re-checking the dimension instead of
        failing.
        """
        name = self._name(project_id)
        if await self._client.collection_exists(name):
            await self._assert_dimension(name, vector_size)
            return
        try:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=vector_size, distance=distance),
            )
        except Exception:
            # A concurrent create between our existence check and this create is
            # not an error: re-check the dimension idempotently. A genuine create
            # failure (collection still absent) re-raises.
            if not await self._client.collection_exists(name):
                raise
            await self._assert_dimension(name, vector_size)

    async def _assert_dimension(self, name: str, vector_size: int) -> None:
        existing = await self._collection_dimension(name)
        if existing is not None and existing != vector_size:
            raise GraphRagCollectionDimensionMismatch(
                f"collection {name} is {existing}-dim but this build produced "
                f"{vector_size}-dim vectors (D2 project embed-dimension drift)"
            )

    async def _collection_dimension(self, name: str) -> int | None:
        """The collection's single-vector size, or ``None`` if undeterminable.

        Returns ``None`` for a named-vectors collection (a shape GraphRAG never
        creates) so the guard fails open rather than on an unexpected schema.
        """
        info = await self._client.get_collection(name)
        params = getattr(getattr(info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        size = getattr(vectors, "size", None)
        return int(size) if isinstance(size, int) else None

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
            collection_name=self._name(project_id),
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
            collection_name=self._name(project_id),
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
            collection_name=self._name(project_id),
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
        name = self._name(project_id)
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

    async def delete_points_not_in_build(
        self,
        *,
        project_id: uuid.UUID,
        config_id: uuid.UUID,
        keep_build_id: uuid.UUID,
    ) -> None:
        """Delete every config point NOT written by ``keep_build_id`` (F-6).

        The build-scoped superset of :meth:`delete_superseded_entities`, for a
        full-corpus Knowledge Map replacement build: every entity the current
        corpus produces is re-embedded under ``keep_build_id``, so any point
        carrying an older ``build_id`` belongs to an entity the corpus no longer
        produces and is an orphan. Unlike the name-scoped supersede it needs no
        entity list — it removes prior-build points regardless of name, cleaning
        up entities that disappeared entirely (which the name-scoped sweep can
        never reach). No-ops if the collection does not exist.
        """
        name = self._name(project_id)
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
        name = self._name(project_id)
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

    async def delete_collection(self, project_id: uuid.UUID) -> bool:
        """Drop ``{prefix}_{project_id}`` entirely. No-ops if it does not exist.

        Returns ``True`` iff a collection was actually dropped, ``False`` when none
        existed -- so the F-3 teardown can tell "dropped" from "absent" rather than
        claiming a drop that never happened. Both are confirmations that the
        collection is gone, and both permit releasing the project's embedding pin.
        Raises on a Qdrant error (module policy: failures fail loudly), which the
        teardown maps to ``TeardownOutcome.FAILED`` and answers by keeping the pin.
        """
        name = self._name(project_id)
        if await self._client.collection_exists(name):
            await self._client.delete_collection(collection_name=name)
            return True
        return False
