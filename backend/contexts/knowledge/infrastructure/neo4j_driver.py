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
        # Capture nodes (name, type, build_id) too so a compensation restore
        # round-trips entity types (audit L1) and brings back isolated nodes.
        node_cypher = (
            "MATCH (n:Entity {graphrag_config_id: $cid}) "
            "RETURN n.name AS name, n.type AS type, n.build_id AS build_id"
        )
        async with driver.session() as session:
            result = await session.run(cypher, cid=str(config_id))
            rows = [dict(rec) async for rec in result]
            node_result = await session.run(node_cypher, cid=str(config_id))
            nodes = [dict(rec) async for rec in node_result]
        return {"edges": rows, "nodes": nodes}

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
        # Audit M3: accumulate evidence and keep the highest confidence rather
        # than last-write-wins. Two rows for the same (subject, relation, object)
        # in one UNWIND — or a restatement across delta builds — previously
        # clobbered prior evidence_msg_ids, which undercut the evidence-excerpt
        # feature. We union evidence (dedup) and take max confidence.
        # Node ``type`` (audit L1) is set when the extractor classified the
        # endpoint (non-empty) and otherwise preserved, so a later mention that
        # omits the type never wipes a known one.
        cypher = (
            "UNWIND $rows AS row "
            "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
            "  ON CREATE SET s.build_id = $bid "
            "SET s.type = CASE WHEN row.subject_type <> '' "
            "             THEN row.subject_type ELSE coalesce(s.type, '') END "
            "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
            "  ON CREATE SET o.build_id = $bid "
            "SET o.type = CASE WHEN row.object_type <> '' "
            "             THEN row.object_type ELSE coalesce(o.type, '') END "
            "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
            "                  relation: row.relation}]->(o) "
            "SET r.build_id = $bid, "
            "    r.confidence = CASE WHEN r.confidence IS NULL "
            "                        OR row.confidence > r.confidence "
            "                   THEN row.confidence ELSE r.confidence END, "
            "    r.evidence_msg_ids = coalesce(r.evidence_msg_ids, []) + "
            "      [x IN row.evidence_msg_ids "
            "         WHERE NOT x IN coalesce(r.evidence_msg_ids, [])]"
        )
        rows = [
            {
                "subject": tr.subject,
                "relation": tr.relation,
                "object": tr.object,
                "confidence": tr.confidence,
                "evidence_msg_ids": [str(x) for x in tr.evidence_msg_ids],
                "subject_type": tr.subject_type,
                "object_type": tr.object_type,
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
        # Audit M4: the relationship leg uses OPTIONAL MATCH so the node-cleanup
        # leg still runs when this build produced only isolated nodes (or its
        # edges were already removed). With a plain MATCH, a no-match on the
        # first pattern short-circuits the WITH and leaves orphan nodes behind
        # on rollback.
        cypher = (
            "OPTIONAL MATCH (:Entity {graphrag_config_id: $cid})"
            "-[r:REL {graphrag_config_id: $cid, build_id: $bid}]->"
            "(:Entity {graphrag_config_id: $cid}) "
            "DELETE r "
            "WITH $cid AS cid, $bid AS bid "
            "MATCH (n:Entity {graphrag_config_id: cid, build_id: bid}) "
            "WHERE COUNT { (n)--() } = 0 DELETE n"
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
        nodes = list(snapshot.get("nodes") or [])
        if not edges and not nodes:
            return
        # Restore nodes (with their type, audit L1) first so isolated nodes come
        # back and edge-restore's ON CREATE never overwrites a type. Older
        # snapshots taken before node capture have no "nodes" key — edge restore
        # alone still rebuilds the connected subgraph.
        node_cypher = (
            "UNWIND $rows AS row "
            "MERGE (n:Entity {graphrag_config_id: $cid, name: row.name}) "
            "SET n.build_id = row.build_id, n.type = coalesce(row.type, '')"
        )
        edge_cypher = (
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
            if nodes:
                await session.run(node_cypher, rows=nodes, cid=str(config_id))
            if edges:
                await session.run(edge_cypher, rows=edges, cid=str(config_id))

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
        # Audit L2: scope the relationship pattern by graphrag_config_id (not
        # just the endpoint nodes) so a future cross-config edge could never
        # leak into one tenant's traversal. Audit M5: ORDER BY confidence so the
        # LIMIT keeps the strongest edges instead of an arbitrary 50.
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid}) "
            "WHERE s.name IN $seeds "
            f"MATCH (s)-[r:REL*1..{h} {{graphrag_config_id: $cid}}]-"
            "(o:Entity {graphrag_config_id: $cid}) "
            "UNWIND r AS edge "
            "RETURN DISTINCT startNode(edge).name AS subject, "
            "                edge.relation AS relation, "
            "                endNode(edge).name AS object, "
            "                edge.confidence AS confidence, "
            "                edge.evidence_msg_ids AS evidence_msg_ids "
            "ORDER BY confidence DESC "
            "LIMIT 50"
        )
        async with driver.session() as session:
            result = await session.run(cypher, cid=str(config_id), seeds=seed_entities)
            return [dict(rec) async for rec in result]

    async def fetch_graph(
        self,
        *,
        config_id: uuid.UUID,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Read the whole config subgraph for visualization.

        Returns ``{"nodes": [...], "edges": [...], "truncated": bool}``. Unlike
        :meth:`snapshot_subgraph` (built for 2PC restore, edges-only, uncapped),
        this returns isolated nodes too and caps both lists so a mature graph
        can never stream tens of thousands of rows into a browser. Nodes are
        ranked by degree, edges by confidence, so a truncated view keeps the
        most connected/confident core.
        """
        node_limit = max(1, limit)
        edge_limit = max(1, limit)
        driver = await self._ensure()
        node_cypher = (
            "MATCH (n:Entity {graphrag_config_id: $cid}) "
            "RETURN n.name AS name, n.build_id AS build_id, n.type AS type, "
            "       COUNT { (n)-[:REL {graphrag_config_id: $cid}]-() } AS degree "
            "ORDER BY degree DESC, name ASC "
            "LIMIT $node_limit"
        )
        edge_cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid})"
            "-[r:REL {graphrag_config_id: $cid}]->"
            "(o:Entity {graphrag_config_id: $cid}) "
            "RETURN s.name AS subject, r.relation AS relation, "
            "       o.name AS object, r.confidence AS confidence "
            "ORDER BY r.confidence DESC, subject ASC "
            "LIMIT $edge_limit"
        )
        async with driver.session() as session:
            node_res = await session.run(node_cypher, cid=str(config_id), node_limit=node_limit)
            nodes = [dict(rec) async for rec in node_res]
            edge_res = await session.run(edge_cypher, cid=str(config_id), edge_limit=edge_limit)
            edges = [dict(rec) async for rec in edge_res]
        truncated = len(nodes) >= node_limit or len(edges) >= edge_limit
        return {"nodes": nodes, "edges": edges, "truncated": truncated}


__all__ = ["Neo4jAsyncDriver"]
