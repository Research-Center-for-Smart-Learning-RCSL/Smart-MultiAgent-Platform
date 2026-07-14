"""Integration tier — F-6 differential replacement against a real Neo4j.

The removal pass and per-build evidence reset are Cypher whose correctness depends
on Neo4j's UNWIND row-write visibility and pre-clause SET evaluation (the mechanism
caveat in the F-6 spec). A FakeNeo4j cannot exercise that — only a real cluster can.
The builder wiring (orchestration) and the Qdrant filter are covered by fast unit
tests; this asserts the graph-shape outcome end-to-end.

Requires a real Neo4j (``SMAP_NEO4J_*``). The repo has no other Neo4j-backed test,
so this runs only where the integration job provisions one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from contexts.knowledge.domain.graphrag import Triple
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

_PROJECT_ID = uuid.uuid4()


@pytest.fixture
async def driver() -> AsyncIterator[Neo4jAsyncDriver]:
    from app.config.settings import get_settings

    s = get_settings()
    d = Neo4jAsyncDriver(uri=s.neo4j.url, auth=(s.neo4j.user, s.neo4j.password))
    try:
        yield d
    finally:
        await d.close()


def _edges_by_key(snapshot: dict) -> dict[tuple[str, str, str], dict]:
    return {(e["subject"], e["relation"], e["object"]): e for e in snapshot["edges"]}


async def _apply(driver: Neo4jAsyncDriver, config_id, build_id, triples) -> None:
    await driver.apply_triples(
        config_id=config_id, project_id=_PROJECT_ID, build_id=build_id, triples=triples, replace=True
    )
    await driver.remove_stale_for_build(config_id=config_id, build_id=build_id)


async def test_replacement_removes_absent_and_recomputes_evidence(driver: Neo4jAsyncDriver) -> None:
    config_id = uuid.uuid4()
    await driver.delete_all(config_id=config_id)

    # Build 1 — docs A + B. Relation R (alice-knows-bob) is co-evidenced by A and B;
    # entity X (xthing-mentions-ything) comes from A only.
    await _apply(
        driver,
        config_id,
        uuid.uuid4(),
        [
            Triple("alice", "knows", "bob", 0.9, ("docA",)),
            Triple("alice", "knows", "bob", 0.8, ("docB",)),
            Triple("xthing", "mentions", "ything", 0.7, ("docA",)),
        ],
    )
    edges = _edges_by_key(await driver.snapshot_subgraph(config_id=config_id, build_id=None))
    assert set(edges[("alice", "knows", "bob")]["evidence_msg_ids"]) == {"docA", "docB"}
    assert ("xthing", "mentions", "ything") in edges

    # Build 2 — doc A removed. The full-corpus rebuild re-reads only B's triples.
    await _apply(driver, config_id, uuid.uuid4(), [Triple("alice", "knows", "bob", 0.8, ("docB",))])
    snap2 = await driver.snapshot_subgraph(config_id=config_id, build_id=None)
    edges2 = _edges_by_key(snap2)
    # R survives; its evidence is recomputed to the live corpus (docA dropped), so
    # the retrieval allowlist no longer hides it for an agent allowed only B.
    assert set(edges2[("alice", "knows", "bob")]["evidence_msg_ids"]) == {"docB"}
    # X and its now-isolated nodes are gone.
    assert ("xthing", "mentions", "ything") not in edges2
    names = {n["name"] for n in snap2["nodes"]}
    assert {"alice", "bob"} <= names
    assert "xthing" not in names
    assert "ything" not in names

    # Build 3 — two surviving docs restate R in the same build: the per-build reset
    # must union WITHIN the build, keeping both refs (the mechanism caveat).
    await _apply(
        driver,
        config_id,
        uuid.uuid4(),
        [
            Triple("alice", "knows", "bob", 0.8, ("docB",)),
            Triple("alice", "knows", "bob", 0.9, ("docC",)),
        ],
    )
    edges3 = _edges_by_key(await driver.snapshot_subgraph(config_id=config_id, build_id=None))
    assert set(edges3[("alice", "knows", "bob")]["evidence_msg_ids"]) == {"docB", "docC"}

    await driver.delete_all(config_id=config_id)
