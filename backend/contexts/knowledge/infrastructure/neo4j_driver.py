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
from collections.abc import Sequence
from typing import Any, cast

from contexts.knowledge.domain.graphrag import Triple


def _collapse_config_rows(
    rows: list[tuple[Any, Any]],
) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
    """Collapse ``(graphrag_config_id, project_id)`` rows to one per config.

    The ``list_config_ids`` DISTINCT is over the *pair*, so a config whose nodes
    carry a mix of NULL and set ``project_id`` (legacy nodes never re-stamped by
    a rebuild) yields two rows for one config. Keep a single entry per config,
    preferring a non-NULL ``project_id`` so the reconciler sweep can Qdrant-purge
    it and never emits a duplicate or misleading orphan-swept audit row.
    """
    by_config: dict[uuid.UUID, uuid.UUID | None] = {}
    for cid_raw, pid_raw in rows:
        if cid_raw is None:
            continue
        cid = uuid.UUID(str(cid_raw))
        pid = uuid.UUID(str(pid_raw)) if pid_raw is not None else None
        if pid is not None or cid not in by_config:
            by_config[cid] = pid
    return list(by_config.items())


# Audit M3: accumulate evidence and keep the highest confidence rather than
# last-write-wins. Two rows for the same (subject, relation, object) in one
# UNWIND — or a restatement across delta builds — previously clobbered prior
# evidence, which undercut the evidence-excerpt feature. We union
# evidence (dedup) and take max confidence.
#
# Phase 2b (R11.22): source_member_ids accumulates the same way, so a relation
# restated by a second member gains that member without losing the first and a
# member-scoped retrieval filter stays correct across delta builds.
#
# Evidence is stored under two keys during the rename to `evidence_refs`
# (docs/tasks/2026-10-12-graphrag-phase5a2-evidence-refs-contract, release B). The
# new key is authoritative; the old one is still written, equal to it, because a
# release A replica in the same rollout reads `evidence_msg_ids` first. The fallback
# to the old key covers an edge the backfill never reached. A SET reads pre-clause
# values, so the two right-hand sides yield the same list.
_EVIDENCE_STORED = "coalesce(r.evidence_refs, r.evidence_msg_ids, [])"
_EVIDENCE_UNION = f"{_EVIDENCE_STORED} + [x IN row.evidence_refs WHERE NOT x IN {_EVIDENCE_STORED}]"
_EVIDENCE_SET_DELTA = (
    f"    r.evidence_msg_ids = {_EVIDENCE_UNION}, "
    f"    r.evidence_refs = {_EVIDENCE_UNION}, "
    "    r.source_member_ids = coalesce(r.source_member_ids, []) + "
    "      [x IN row.source_member_ids "
    "         WHERE NOT x IN coalesce(r.source_member_ids, [])], "
)

# F-6: for a full-corpus knowmap replacement build, evidence and member
# provenance must reflect ONLY the current corpus, not accumulate across builds —
# otherwise a relation co-evidenced by a deleted and a surviving document keeps
# the deleted document's ref, which the retrieval allowlist then uses to hide a
# still-valid relation. The reset is scoped to the current build: the FIRST touch
# of a relation this build (its ``r.build_id`` still holds a prior value, or NULL
# for a freshly-MERGEd relation) resets to the incoming row's values; a LATER
# touch within the SAME build (``r.build_id`` already advanced to ``$bid`` by an
# earlier row) unions, so a relation restated across surviving rows keeps all its
# live refs. This relies on UNWIND applying earlier rows' writes before later rows
# and on the SET right-hand sides reading pre-clause property values — both hold
# in Neo4j and are asserted by the F-6 regression tests.
_EVIDENCE_REPLACE = f"CASE WHEN r.build_id = $bid THEN {_EVIDENCE_UNION} ELSE row.evidence_refs END"
_EVIDENCE_SET_REPLACE = (
    f"    r.evidence_msg_ids = {_EVIDENCE_REPLACE}, "
    f"    r.evidence_refs = {_EVIDENCE_REPLACE}, "
    "    r.source_member_ids = CASE WHEN r.build_id = $bid THEN "
    "        coalesce(r.source_member_ids, []) + "
    "        [x IN row.source_member_ids WHERE NOT x IN coalesce(r.source_member_ids, [])] "
    "      ELSE row.source_member_ids END, "
)

# F-6 differential replacement: drop every relation the current build did not
# touch, then the entities that leaves isolated.
#
# ``_upsert_cypher`` sets ``r.build_id = $bid`` on EVERY relation it touches, so
# afterwards every current relation carries the current build id and every stale
# one carries an older id (or NULL for a legacy edge).
#
# Entity liveness is keyed on degree-0-after-relation-removal, NOT on entity
# ``build_id``: entity ``build_id`` is set ``ON CREATE`` only, so a re-touched live
# entity keeps a stale id and a build_id-based delete would remove live entities.
# The upsert only ever creates an entity as a relation endpoint, so an entity with
# no surviving relation is genuinely absent from the current corpus.
#
# OPTIONAL MATCH so the node-cleanup leg still runs when no relation is stale
# (e.g. only isolated nodes remain). DISTINCT collapses the one-row-per-deleted-
# relation stream to a single row, so the degree-0 cleanup scans the config's
# entities once, not once per stale relation (idempotent either way, but the
# un-DISTINCT'd form is a stale-rels x entities cartesian).
_PRUNE_STALE_CYPHER = (
    "OPTIONAL MATCH (:Entity {graphrag_config_id: $cid})"
    "-[r:REL {graphrag_config_id: $cid}]->"
    "(:Entity {graphrag_config_id: $cid}) "
    "WHERE r.build_id IS NULL OR r.build_id <> $bid "
    "DELETE r "
    "WITH DISTINCT $cid AS cid "
    "MATCH (n:Entity {graphrag_config_id: cid}) "
    "WHERE COUNT { (n)--() } = 0 DELETE n"
)


def _build_upsert_cypher(*, replace: bool) -> str:
    """Triple-upsert Cypher, shared by the delta apply and atomic replacement.

    ``replace`` switches evidence/member provenance from the cross-build union
    (Concept Map delta) to the per-build reset described on
    :data:`_EVIDENCE_SET_REPLACE`. Called twice at import time; the callers use
    the resulting constants, not this builder.

    Node ``type`` (audit L1): a specific classification wins; an empty type
    (unknown) or the catch-all 'other' never downgrades a known specific type
    (audit review #4, the extractor maps low-confidence guesses to 'other', so
    without this a later 'other' would clobber an earlier 'person'). 'other' only
    fills a node that has no type yet.
    """
    return (
        "UNWIND $rows AS row "
        "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
        "  ON CREATE SET s.build_id = $bid "
        "SET s.project_id = $pid, "
        "    s.type = CASE "
        "  WHEN row.subject_type = '' THEN coalesce(s.type, '') "
        "  WHEN row.subject_type = 'other' AND coalesce(s.type, '') <> '' THEN s.type "
        "  ELSE row.subject_type END "
        "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
        "  ON CREATE SET o.build_id = $bid "
        "SET o.project_id = $pid, "
        "    o.type = CASE "
        "  WHEN row.object_type = '' THEN coalesce(o.type, '') "
        "  WHEN row.object_type = 'other' AND coalesce(o.type, '') <> '' THEN o.type "
        "  ELSE row.object_type END "
        "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
        "                  relation: row.relation}]->(o) "
        "SET r.build_id = $bid, "
        "    r.confidence = CASE WHEN r.confidence IS NULL "
        "                        OR row.confidence > r.confidence "
        "                   THEN row.confidence ELSE r.confidence END, "
        + (_EVIDENCE_SET_REPLACE if replace else _EVIDENCE_SET_DELTA)
        # Phase 2b WS5 (R11.21): keep first_seen earliest / last_seen latest across
        # delta builds and restatements. A NULL incoming stamp never clobbers a set
        # one; the values are UTC epoch seconds (numeric), so < / > compare
        # directly. Mirrors the confidence-max merge above.
        + "    r.first_seen_at = CASE WHEN r.first_seen_at IS NULL "
        "        OR (row.first_seen_at IS NOT NULL AND row.first_seen_at < r.first_seen_at) "
        "      THEN row.first_seen_at ELSE r.first_seen_at END, "
        "    r.last_seen_at = CASE WHEN r.last_seen_at IS NULL "
        "        OR (row.last_seen_at IS NOT NULL AND row.last_seen_at > r.last_seen_at) "
        "      THEN row.last_seen_at ELSE r.last_seen_at END"
    )


# Restore nodes (with their type, audit L1) first so isolated nodes come back and
# edge-restore's ON CREATE never overwrites a type. Older snapshots taken before
# node capture have no "nodes" key — edge restore alone still rebuilds the
# connected subgraph.
_RESTORE_NODES_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (n:Entity {graphrag_config_id: $cid, name: row.name}) "
    "SET n.build_id = row.build_id, n.type = coalesce(row.type, ''), "
    # coalesce so an older snapshot (captured before project_id was
    # self-describing) does not null out a value a later build set.
    "    n.project_id = coalesce(row.project_id, n.project_id)"
)

_RESTORE_EDGES_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
    "  ON CREATE SET s.build_id = row.build_id "
    "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
    "  ON CREATE SET o.build_id = row.build_id "
    "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
    "                  relation: row.relation}]->(o) "
    "SET r.build_id = row.build_id, "
    "    r.confidence = row.confidence, "
    # Either key: a snapshot from before the rename carries only evidence_msg_ids,
    # and one from a release past it may carry only evidence_refs.
    "    r.evidence_msg_ids = coalesce(row.evidence_refs, row.evidence_msg_ids), "
    "    r.evidence_refs = coalesce(row.evidence_refs, row.evidence_msg_ids), "
    # coalesce so an older snapshot (taken before member provenance existed)
    # restores to [] rather than nulling the property.
    "    r.source_member_ids = coalesce(row.source_member_ids, []), "
    # WS5: restore the timestamps verbatim. An older snapshot has no such keys, so
    # row.first_seen_at/last_seen_at are NULL — a timeless edge, which retrieval
    # treats as pure-confidence (no decay).
    "    r.first_seen_at = row.first_seen_at, "
    "    r.last_seen_at = row.last_seen_at"
)


_UPSERT_CYPHER_DELTA = _build_upsert_cypher(replace=False)
_UPSERT_CYPHER_REPLACE = _build_upsert_cypher(replace=True)


# Every read surfaces evidence under the domain name, so the physical spelling stops
# at this module.
_EVIDENCE_READ = "coalesce({r}.evidence_refs, {r}.evidence_msg_ids)"

# An edge the backfill still has to copy: written by N-1 (no new key) or touched by an
# N-1 writer after a copy (new key stale). Releases A and B always write both keys
# equal, so a difference can only come from N-1, whose old key is then the truth.
_EVIDENCE_UNMIGRATED = (
    "r.evidence_msg_ids IS NOT NULL AND (r.evidence_refs IS NULL OR r.evidence_refs <> r.evidence_msg_ids)"
)


def _triple_rows(triples: list[Triple]) -> list[dict[str, Any]]:
    """Flatten domain triples into the UNWIND row payload."""
    return [
        {
            "subject": tr.subject,
            "relation": tr.relation,
            "object": tr.object,
            "confidence": tr.confidence,
            "evidence_refs": list(tr.evidence_refs),
            "subject_type": tr.subject_type,
            "object_type": tr.object_type,
            "source_member_ids": list(tr.source_member_ids),
            "first_seen_at": tr.first_seen_at,
            "last_seen_at": tr.last_seen_at,
        }
        for tr in triples
    ]


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
        evidence = _EVIDENCE_READ.format(r="r")
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid})"
            "-[r:REL]->(o:Entity {graphrag_config_id: $cid}) "
            "RETURN s.name AS subject, r.relation AS relation, "
            "o.name AS object, r.confidence AS confidence, "
            # The snapshot is data that crosses versions (Redis, 24 h): a release A
            # restore reads evidence_msg_ids from it first, so it carries both keys.
            f"{evidence} AS evidence_refs, {evidence} AS evidence_msg_ids, "
            "r.source_member_ids AS source_member_ids, "
            "r.first_seen_at AS first_seen_at, r.last_seen_at AS last_seen_at, "
            "r.build_id AS build_id"
        )
        # Capture nodes (name, type, build_id) too so a compensation restore
        # round-trips entity types (audit L1) and brings back isolated nodes.
        node_cypher = (
            "MATCH (n:Entity {graphrag_config_id: $cid}) "
            "RETURN n.name AS name, n.type AS type, n.build_id AS build_id, "
            "n.project_id AS project_id"
        )
        cid = str(config_id)

        # One transaction, for the same reason the writes use one: this is the
        # compensation material, and edges read at a different instant from nodes
        # would restore a subgraph that never existed (edges whose endpoints the
        # node list omits, or nodes whose edges it does not). The per-config build
        # lock normally serializes writers, but it is a Redis key with a TTL that
        # is refreshed on a timer, so a lapsed lock reopens the window.
        async def _read(tx: Any) -> dict[str, Any]:
            edge_result = await tx.run(cypher, cid=cid)
            edges = [dict(rec) async for rec in edge_result]
            node_result = await tx.run(node_cypher, cid=cid)
            nodes = [dict(rec) async for rec in node_result]
            return {"edges": edges, "nodes": nodes}

        async with driver.session() as session:
            return cast(dict[str, Any], await session.execute_read(_read))

    async def apply_triples(
        self,
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
        build_id: uuid.UUID,
        triples: list[Triple],
    ) -> int:
        if not triples:
            return 0
        driver = await self._ensure()
        async with driver.session() as session:
            await session.run(
                _UPSERT_CYPHER_DELTA,
                rows=_triple_rows(triples),
                cid=str(config_id),
                pid=str(project_id),
                bid=str(build_id),
            )
        return len(triples)

    async def replace_triples(
        self,
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
        build_id: uuid.UUID,
        triples: list[Triple],
    ) -> int:
        """Upsert + stale prune in ONE managed transaction (see the port docstring).

        ``execute_write`` rather than two auto-commit ``session.run`` calls: the
        prune must not be able to fail after the upsert is already durable. It may
        re-run the callback on a retryable error, which is safe because both
        statements are keyed on ``build_id`` and so are idempotent for this build.

        This is the only managed transaction in the adapter; every other method is
        a single statement, for which auto-commit is equivalent.
        """
        driver = await self._ensure()
        rows = _triple_rows(triples)
        cid, pid, bid = str(config_id), str(project_id), str(build_id)

        async def _replace(tx: Any) -> None:
            # An empty corpus still prunes: with no upsert, every existing relation
            # carries a stale build_id, so the prune is what empties the subgraph.
            if rows:
                await tx.run(_UPSERT_CYPHER_REPLACE, rows=rows, cid=cid, pid=pid, bid=bid)
            await tx.run(_PRUNE_STALE_CYPHER, cid=cid, bid=bid)

        async with driver.session() as session:
            await session.execute_write(_replace)
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
            f"r.confidence AS confidence, {_EVIDENCE_READ.format(r='r')} AS evidence_refs, "
            "r.source_member_ids AS source_member_ids, "
            "r.first_seen_at AS first_seen_at, r.last_seen_at AS last_seen_at"
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

    async def list_config_ids(
        self,
    ) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
        driver = await self._ensure()
        cypher = "MATCH (n:Entity) RETURN DISTINCT n.graphrag_config_id AS cid, n.project_id AS pid"
        async with driver.session() as session:
            result = await session.run(cypher)
            rows = [(rec["cid"], rec["pid"]) async for rec in result]
        return _collapse_config_rows(rows)

    async def count_unmigrated_evidence(self) -> dict[str, int]:
        """Edges per raw config id whose ``evidence_refs`` does not yet mirror the old key.

        For the ``neo4j-evidence-refs`` maintenance command only. Keys are left
        unparsed (``""`` for a missing id) so the caller can count an edge it
        cannot attribute instead of losing it from the remaining total. A full
        relationship scan: no index covers the property comparison.
        """
        driver = await self._ensure()
        cypher = (
            f"MATCH ()-[r:REL]->() WHERE {_EVIDENCE_UNMIGRATED} "
            "RETURN r.graphrag_config_id AS cid, count(r) AS n"
        )
        async with driver.session() as session:
            result = await session.run(cypher)
            counts: dict[str, int] = {}
            async for rec in result:
                key = "" if rec["cid"] is None else str(rec["cid"])
                counts[key] = counts.get(key, 0) + int(rec["n"])
            return counts

    async def copy_evidence_refs(self, *, config_id: uuid.UUID, batch: int) -> int:
        """Copy the old key to ``evidence_refs`` on up to ``batch`` edges of one config.

        Returns how many edges it copied; the caller loops until 0, holding the
        config's build lock so no build writes the same edges meanwhile.
        """
        driver = await self._ensure()
        # `r.relation IS NOT NULL` holds for every REL (it is a MERGE key); it is
        # here so the planner seeks the (graphrag_config_id, relation) index
        # instead of scanning every relationship once per batch.
        cypher = (
            "MATCH ()-[r:REL {graphrag_config_id: $cid}]->() "
            f"WHERE r.relation IS NOT NULL AND {_EVIDENCE_UNMIGRATED} "
            "WITH r LIMIT $batch "
            "SET r.evidence_refs = r.evidence_msg_ids "
            "RETURN count(r) AS n"
        )
        async with driver.session() as session:
            result = await session.run(cypher, cid=str(config_id), batch=batch)
            record = await result.single()
        return int(record["n"]) if record is not None else 0

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
        cid = str(config_id)

        # One transaction so a restore is all-or-nothing. A failure between the two
        # legs would otherwise leave the entities back without their relations. The
        # reconciler does retry (it keeps the snapshot and preserves the stuck state,
        # which stays read-blocked), so that skeleton is not normally served — but an
        # admin reset with force=true lands a failed compensation on IDLE, where it
        # would be. Restoring nothing is a strictly better starting point for the
        # retry either way.
        async def _restore(tx: Any) -> None:
            if nodes:
                await tx.run(_RESTORE_NODES_CYPHER, rows=nodes, cid=cid)
            if edges:
                await tx.run(_RESTORE_EDGES_CYPHER, rows=edges, cid=cid)

        async with driver.session() as session:
            await session.execute_write(_restore)

    async def traverse(
        self,
        *,
        config_id: uuid.UUID,
        seed_entities: list[str],
        hops: int,
        member_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not seed_entities or (member_ids is not None and not member_ids):
            return []
        h = max(1, min(hops, 2))
        driver = await self._ensure()
        # R11.22 member filter: every edge on the path must name a chosen member,
        # so a hidden edge can neither be returned nor lead to a visible one two
        # hops out. Applied in the MATCH, before the LIMIT, so hidden edges never
        # take the 50 slots.
        member_where = (
            "WHERE all(e IN r WHERE any(x IN coalesce(e.source_member_ids, []) WHERE x IN $members)) "
            if member_ids is not None
            else ""
        )
        # Audit L2: scope the relationship pattern by graphrag_config_id (not
        # just the endpoint nodes) so a future cross-config edge could never
        # leak into one tenant's traversal. Audit M5: ORDER BY confidence so the
        # LIMIT keeps the strongest edges instead of an arbitrary 50.
        cypher = (
            "MATCH (s:Entity {graphrag_config_id: $cid}) "
            "WHERE s.name IN $seeds "
            f"MATCH (s)-[r:REL*1..{h} {{graphrag_config_id: $cid}}]-"
            "(o:Entity {graphrag_config_id: $cid}) "
            f"{member_where}"
            "UNWIND r AS edge "
            "RETURN DISTINCT startNode(edge).name AS subject, "
            "                edge.relation AS relation, "
            "                endNode(edge).name AS object, "
            "                edge.confidence AS confidence, "
            f"                {_EVIDENCE_READ.format(r='edge')} AS evidence_refs, "
            "                edge.source_member_ids AS source_member_ids, "
            "                edge.first_seen_at AS first_seen_at, "
            "                edge.last_seen_at AS last_seen_at "
            # coalesce: a NULL confidence sorts as the largest value in Neo4j, so
            # legacy/restored edges with no confidence would otherwise consume the
            # LIMIT ahead of genuinely strong edges. Treat missing as 0.0.
            "ORDER BY coalesce(confidence, 0.0) DESC "
            "LIMIT 50"
        )
        async with driver.session() as session:
            result = await session.run(
                cypher, cid=str(config_id), seeds=seed_entities, members=list(member_ids or [])
            )
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
            # coalesce: NULL sorts highest under DESC in Neo4j, so a missing
            # confidence must not outrank real edges in the truncated view.
            "ORDER BY coalesce(r.confidence, 0.0) DESC, subject ASC "
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
