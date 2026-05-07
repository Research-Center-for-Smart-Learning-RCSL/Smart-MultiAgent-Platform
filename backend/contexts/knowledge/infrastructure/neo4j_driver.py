"""Neo4j async driver adapter for the GraphRAG context (E.7/E.8).

Wraps :class:`neo4j.AsyncDriver` with the minimum surface the builder,
reconciler, and retrieve service need. All entities carry
``graphrag_config_id`` and ``build_id`` properties so deletes + snapshots
can scope precisely.

The ``neo4j`` import is kept inside methods rather than at module level
so unit tests that only import application-layer classes never pull in
the client.
"""

from __future__ import annotations

import uuid
from typing import Any

from contexts.knowledge.domain.graphrag import Triple


class Neo4jAsyncDriver:
    """Adapter implementing :class:`Neo4jDriver` against a real cluster."""

    def __init__(self, *, uri: str, auth: tuple[str, str]) -> None:
        self._uri = uri
        self._auth = auth
        self._driver: Any | None = None

    async def _ensure(self) -> Any:
        if self._driver is None:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(self._uri, auth=self._auth)
        return self._driver

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def snapshot_subgraph(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        driver = await self._ensure()
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid})"
            "-[r:REL]->(o:Entity {graphrag_config_id: $cid}) "
            "RETURN s.name AS subject, r.relation AS relation, "
            "o.name AS object, r.confidence AS confidence, "
            "r.evidence_msg_ids AS evidence_msg_ids, "
            "r.build_id AS build_id"
        )
        async with driver.session() as session:
            result = await session.run(cypher, cid=str(config_id))
            rows = [dict(rec) async for rec in result]
        return {"edges": rows}

    async def apply_triples(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        triples: list[Triple],
    ) -> int:
        if not triples:
            return 0
        driver = await self._ensure()
        cypher = (
            "UNWIND $rows AS row "
            "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
            "  ON CREATE SET s.build_id = $bid "
            "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
            "  ON CREATE SET o.build_id = $bid "
            "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
            "                  relation: row.relation}]->(o) "
            "SET r.build_id = $bid, "
            "    r.confidence = row.confidence, "
            "    r.evidence_msg_ids = row.evidence_msg_ids"
        )
        rows = [
            {
                "subject": tr.subject,
                "relation": tr.relation,
                "object": tr.object,
                "confidence": tr.confidence,
                "evidence_msg_ids": [str(x) for x in tr.evidence_msg_ids],
            }
            for tr in triples
        ]
        async with driver.session() as session:
            await session.run(
                cypher,
                rows=rows,
                cid=str(config_id),
                bid=str(build_id),
            )
        return len(triples)

    async def delete_by_build(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> None:
        driver = await self._ensure()
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid})"
            "-[r:REL {build_id: $bid}]->(o:Entity {graphrag_config_id: $cid}) "
            "DELETE r "
            "WITH $cid AS cid, $bid AS bid "
            "MATCH (n:Entity {graphrag_config_id: cid, build_id: bid}) "
            "WHERE NOT (n)--() DELETE n"
        )
        async with driver.session() as session:
            await session.run(cypher, cid=str(config_id), bid=str(build_id))

    async def list_triples_for_build(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Return all triples whose REL is tagged with build_id (for Phase-2 retry)."""
        driver = await self._ensure()
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid})"
            "-[r:REL {graphrag_config_id: $cid, build_id: $bid}]->"
            "(o:Entity {graphrag_config_id: $cid}) "
            "RETURN s.name AS subject, r.relation AS relation, o.name AS object, "
            "r.confidence AS confidence, r.evidence_msg_ids AS evidence_msg_ids"
        )
        async with driver.session() as session:
            result = await session.run(
                cypher,
                cid=str(config_id),
                bid=str(build_id),
            )
            return [dict(rec) async for rec in result]

    async def delete_all(self, *, config_id: uuid.UUID) -> None:
        driver = await self._ensure()
        cypher = "MATCH (n:Entity {graphrag_config_id: $cid}) DETACH DELETE n"
        async with driver.session() as session:
            await session.run(cypher, cid=str(config_id))

    async def restore_from_snapshot(
        self,
        *,
        config_id: uuid.UUID,
        snapshot: dict[str, Any],
    ) -> None:
        driver = await self._ensure()
        edges = list(snapshot.get("edges") or [])
        if not edges:
            return
        cypher = (
            "UNWIND $rows AS row "
            "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
            "  ON CREATE SET s.build_id = row.build_id "
            "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
            "  ON CREATE SET o.build_id = row.build_id "
            "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
            "                  relation: row.relation}]->(o) "
            "SET r.build_id = row.build_id, "
            "    r.confidence = row.confidence, "
            "    r.evidence_msg_ids = row.evidence_msg_ids"
        )
        async with driver.session() as session:
            await session.run(cypher, rows=edges, cid=str(config_id))

    async def traverse(
        self,
        *,
        config_id: uuid.UUID,
        seed_entities: list[str],
        hops: int,
    ) -> list[dict[str, Any]]:
        if not seed_entities:
            return []
        h = max(1, min(hops, 2))
        driver = await self._ensure()
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid}) "
            "WHERE s.name IN $seeds "
            f"MATCH (s)-[r:REL*1..{h}]-(o:Entity {{graphrag_config_id: $cid}}) "
            "UNWIND r AS edge "
            "RETURN DISTINCT startNode(edge).name AS subject, "
            "                edge.relation AS relation, "
            "                endNode(edge).name AS object, "
            "                edge.confidence AS confidence, "
            "                edge.evidence_msg_ids AS evidence_msg_ids "
            "LIMIT 50"
        )
        async with driver.session() as session:
            result = await session.run(cypher, cid=str(config_id), seeds=seed_entities)
            return [dict(rec) async for rec in result]


__all__ = ["Neo4jAsyncDriver"]
