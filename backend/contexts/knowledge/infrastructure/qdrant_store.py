"""Thin Qdrant wrapper for RAG collections.

Each project gets one ``rag_{project_id}`` collection (§21.4). The payload
is ``{doc_id, chunk_idx, agent_ids: [...]}`` — the ``agent_ids`` array is
how retrieval filters results to agents allowed to see a chunk when a
config is bound to a subset of a project's agents.

SoC:
- The wrapper owns collection naming, client lifecycle, and *only* the
  operations the ingest + retrieve paths need (upsert, search, delete).
- Retries / circuit-breaking are NOT done here — the caller lives inside
  a request with its own timeout budget, and Qdrant failures fail loudly.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

__all__ = ["QdrantHit", "QdrantStore", "collection_name"]


def collection_name(project_id: uuid.UUID) -> str:
    # Qdrant collection names permit alphanumerics + `-` + `_`; UUIDs with
    # dashes are fine, but we normalise to underscores to match §21.4 exactly.
    return f"rag_{str(project_id).replace('-', '_')}"


@dataclass(frozen=True, slots=True)
class QdrantHit:
    point_id: uuid.UUID
    score: float
    payload: dict[str, Any]


class QdrantStore:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_collection(
        self,
        project_id: uuid.UUID,
        *,
        vector_size: int,
        distance: Distance = Distance.COSINE,
    ) -> None:
        name = collection_name(project_id)
        existing = await self._client.collection_exists(name)
        if existing:
            return
        await self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=distance),
        )

    async def upsert_chunks(
        self,
        *,
        project_id: uuid.UUID,
        points: Iterable[tuple[uuid.UUID, list[float], dict[str, Any]]],
    ) -> None:
        structs = [PointStruct(id=str(pid), vector=vec, payload=payload) for (pid, vec, payload) in points]
        if not structs:
            return
        await self._client.upsert(
            collection_name=collection_name(project_id),
            points=structs,
            wait=True,
        )

    async def search(
        self,
        *,
        project_id: uuid.UUID,
        query_vector: list[float],
        top_k: int,
        agent_id: uuid.UUID | None = None,
        doc_ids: list[uuid.UUID] | None = None,
    ) -> list[QdrantHit]:
        must: list[FieldCondition] = []
        if agent_id is not None:
            must.append(
                FieldCondition(
                    key="agent_ids",
                    match=MatchAny(any=[str(agent_id)]),
                )
            )
        if doc_ids:
            must.append(
                FieldCondition(
                    key="doc_id",
                    match=MatchAny(any=[str(d) for d in doc_ids]),
                )
            )
        qfilter = Filter(must=must) if must else None
        results = await self._client.search(
            collection_name=collection_name(project_id),
            query_vector=query_vector,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        out: list[QdrantHit] = []
        for r in results:
            pid = r.id
            if not isinstance(pid, uuid.UUID):  # type: ignore[unreachable]
                try:
                    pid = uuid.UUID(str(pid))  # type: ignore[assignment]
                except ValueError:
                    continue
            out.append(
                QdrantHit(
                    point_id=pid,  # type: ignore[arg-type]
                    score=float(r.score or 0.0),
                    payload=dict(r.payload or {}),
                )
            )
        return out

    async def delete_document(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        await self._client.delete(
            collection_name=collection_name(project_id),
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="doc_id",
                        match=MatchValue(value=str(document_id)),
                    )
                ]
            ),
            wait=True,
        )

    async def delete_documents(
        self,
        *,
        project_id: uuid.UUID,
        document_ids: Iterable[uuid.UUID],
    ) -> None:
        """Delete every point belonging to any of ``document_ids`` in one call.

        Used by the RAG config-delete cascade (DOM-1), where a single config
        can own many documents. No-ops on an empty id list or a missing
        collection so the caller never has to special-case those.
        """
        ids = [str(d) for d in document_ids]
        if not ids:
            return
        name = collection_name(project_id)
        if not await self._client.collection_exists(name):
            return
        await self._client.delete(
            collection_name=name,
            points_selector=Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=ids))]),
            wait=True,
        )
