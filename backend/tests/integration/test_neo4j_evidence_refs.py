"""Relation evidence under ``evidence_refs``, on real Neo4j.

What remains of docs/tasks/2026-10-12-graphrag-phase5a2-evidence-refs-contract once its
clean-up removed the old property: every read, write, snapshot and restore uses the
one key, and a snapshot round-trips it intact.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from contexts.knowledge.domain.graphrag import Triple
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

pytestmark = pytest.mark.db

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


@pytest.fixture
async def config_id(driver: Neo4jAsyncDriver) -> AsyncIterator[uuid.UUID]:
    cid = uuid.uuid4()
    try:
        yield cid
    finally:
        await driver.delete_all(config_id=cid)


async def _evidence(driver: Neo4jAsyncDriver, cid: uuid.UUID) -> list[list[str]]:
    rows = await driver.traverse(config_id=cid, seed_entities=["alice"], hops=1)
    return [sorted(r["evidence_refs"]) for r in rows]


async def test_a_delta_write_unions_evidence_across_builds(
    driver: Neo4jAsyncDriver, config_id: uuid.UUID
) -> None:
    for refs in (("m1",), ("m2", "m1")):
        await driver.apply_triples(
            config_id=config_id,
            project_id=_PROJECT_ID,
            build_id=uuid.uuid4(),
            triples=[Triple("alice", "knows", "bob", 0.8, refs)],
        )
    assert await _evidence(driver, config_id) == [["m1", "m2"]]


async def test_a_replace_write_keeps_only_the_current_builds_evidence(
    driver: Neo4jAsyncDriver, config_id: uuid.UUID
) -> None:
    await driver.replace_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[Triple("alice", "knows", "bob", 0.8, ("docA",))],
    )
    await driver.replace_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[
            Triple("alice", "knows", "bob", 0.8, ("docB",)),
            Triple("alice", "knows", "bob", 0.9, ("docC",)),
        ],
    )
    assert await _evidence(driver, config_id) == [["docB", "docC"]]


async def test_a_snapshot_restores_its_evidence(driver: Neo4jAsyncDriver, config_id: uuid.UUID) -> None:
    await driver.apply_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[Triple("alice", "knows", "bob", 0.9, ("m1", "m2"))],
    )
    snapshot = await driver.snapshot_subgraph(config_id=config_id, build_id=None)
    await driver.delete_all(config_id=config_id)

    await driver.restore_from_snapshot(config_id=config_id, snapshot=snapshot)

    assert await _evidence(driver, config_id) == [["m1", "m2"]]
