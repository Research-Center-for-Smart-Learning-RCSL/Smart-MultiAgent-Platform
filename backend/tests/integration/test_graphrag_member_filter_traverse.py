"""The member filter inside the traversal Cypher, on real Neo4j (R11.22, AC-3).

A path is kept only when every edge on it names a chosen member, so a hidden
edge cannot be returned and cannot be the way to a visible edge two hops out.
The predicate sits before ``LIMIT 50``; an edge with no provenance never matches.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from contexts.knowledge.domain.graphrag import Triple
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

pytestmark = pytest.mark.db

A, B = "member-a", "member-b"


@pytest.fixture
async def driver() -> AsyncIterator[Neo4jAsyncDriver]:
    from app.config.settings import get_settings

    s = get_settings()
    d = Neo4jAsyncDriver(uri=s.neo4j.url, auth=(s.neo4j.user, s.neo4j.password))
    try:
        yield d
    finally:
        await d.close()


@pytest.fixture
async def config_id(driver: Neo4jAsyncDriver) -> AsyncIterator[uuid.UUID]:
    cid = uuid.uuid4()
    # alice -knows[A]-> bob -likes[B]-> carol ; alice -sees[B]-> dave ;
    # alice -legacy[no provenance]-> eve
    await driver.apply_triples(
        config_id=cid,
        project_id=uuid.uuid4(),
        build_id=uuid.uuid4(),
        triples=[
            Triple("alice", "knows", "bob", 0.9, ("m1",), source_member_ids=(A,)),
            Triple("bob", "likes", "carol", 0.8, ("m2",), source_member_ids=(B,)),
            Triple("alice", "sees", "dave", 0.7, ("m3",), source_member_ids=(B,)),
            Triple("alice", "legacy", "eve", 0.6, ("m4",)),
        ],
    )
    try:
        yield cid
    finally:
        await driver.delete_all(config_id=cid)


async def _relations(driver: Neo4jAsyncDriver, cid: uuid.UUID, members: list[str] | None) -> set[str]:
    rows = await driver.traverse(config_id=cid, seed_entities=["alice"], hops=2, member_ids=members)
    return {r["relation"] for r in rows}


async def test_unfiltered_reads_every_reachable_edge(driver: Neo4jAsyncDriver, config_id: uuid.UUID) -> None:
    assert await _relations(driver, config_id, None) == {"knows", "likes", "sees", "legacy"}


async def test_a_hidden_edge_does_not_lead_to_a_visible_one(
    driver: Neo4jAsyncDriver, config_id: uuid.UUID
) -> None:
    # "likes" names B, but the only path to it runs through "knows" (A only).
    assert await _relations(driver, config_id, [B]) == {"sees"}


async def test_intersection_keeps_a_path_whose_edges_each_name_a_chosen_member(
    driver: Neo4jAsyncDriver, config_id: uuid.UUID
) -> None:
    assert await _relations(driver, config_id, [A, B]) == {"knows", "likes", "sees"}


async def test_an_empty_filter_reads_nothing(driver: Neo4jAsyncDriver, config_id: uuid.UUID) -> None:
    assert await _relations(driver, config_id, []) == set()
