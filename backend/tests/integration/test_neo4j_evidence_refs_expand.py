"""Release A of the Neo4j ``evidence_msg_ids`` -> ``evidence_refs`` rename, on real Neo4j.

``docs/tasks/2026-10-12-graphrag-phase5a-evidence-refs-expand``. During release A the
old key stays authoritative (an N-1 replica may still be writing only it), every
write keeps both keys equal, every read surfaces the domain name ``evidence_refs``,
and a compensation snapshot restores intact across the version boundary in either
direction. The backfill command copies legacy edges under each config's build lock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from redis.asyncio import Redis

from contexts.knowledge.domain.graphrag import Triple
from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
from contexts.knowledge.infrastructure.redis_lock import RedisBuildLockStore

pytestmark = pytest.mark.db

_PROJECT_ID = uuid.uuid4()

# Release N-1's edge restore, verbatim, so a snapshot written by release A is
# checked against the code an N-1 reconciler would really run.
_N_MINUS_1_RESTORE_EDGES_CYPHER = (
    "UNWIND $rows AS row "
    "MERGE (s:Entity {graphrag_config_id: $cid, name: row.subject}) "
    "  ON CREATE SET s.build_id = row.build_id "
    "MERGE (o:Entity {graphrag_config_id: $cid, name: row.object}) "
    "  ON CREATE SET o.build_id = row.build_id "
    "MERGE (s)-[r:REL {graphrag_config_id: $cid, "
    "                  relation: row.relation}]->(o) "
    "SET r.build_id = row.build_id, "
    "    r.confidence = row.confidence, "
    "    r.evidence_msg_ids = row.evidence_msg_ids, "
    "    r.source_member_ids = coalesce(row.source_member_ids, []), "
    "    r.first_seen_at = row.first_seen_at, "
    "    r.last_seen_at = row.last_seen_at"
)


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
async def raw() -> AsyncIterator[AsyncDriver]:
    """A plain client for writing legacy shapes and reading raw properties."""
    from app.config.settings import get_settings

    s = get_settings()
    d = AsyncGraphDatabase.driver(s.neo4j.url, auth=(s.neo4j.user, s.neo4j.password))
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


async def _legacy_edge(raw: AsyncDriver, cid: uuid.UUID, refs: list[str], *, rel: str = "knows") -> None:
    """An edge as release N-1 leaves it: evidence only under the old key."""
    async with raw.session() as session:
        await session.run(
            "MERGE (s:Entity {graphrag_config_id: $cid, name: 'alice'}) "
            "  ON CREATE SET s.build_id = 'b0', s.project_id = $pid "
            "MERGE (o:Entity {graphrag_config_id: $cid, name: 'bob'}) "
            "  ON CREATE SET o.build_id = 'b0', o.project_id = $pid "
            "MERGE (s)-[r:REL {graphrag_config_id: $cid, relation: $rel}]->(o) "
            "SET r.build_id = 'b0', r.confidence = 0.9, r.evidence_msg_ids = $refs, "
            "    r.source_member_ids = []",
            cid=str(cid),
            pid=str(_PROJECT_ID),
            rel=rel,
            refs=refs,
        )


async def _raw_evidence(raw: AsyncDriver, cid: uuid.UUID) -> dict[str, tuple[Any, Any]]:
    """relation -> (evidence_msg_ids, evidence_refs) as stored."""
    async with raw.session() as session:
        result = await session.run(
            "MATCH ()-[r:REL {graphrag_config_id: $cid}]->() "
            "RETURN r.relation AS rel, r.evidence_msg_ids AS old, r.evidence_refs AS new",
            cid=str(cid),
        )
        return {rec["rel"]: (rec["old"], rec["new"]) async for rec in result}


def _sorted(value: Any) -> Any:
    return sorted(value) if value is not None else None


# ---------------------------------------------------------------------------
# Reads (AC-2)
# ---------------------------------------------------------------------------


async def test_a_legacy_edge_reads_with_its_evidence(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    await _legacy_edge(raw, config_id, ["m1", "m2"])

    rows = await driver.traverse(config_id=config_id, seed_entities=["alice"], hops=1)
    assert [sorted(r["evidence_refs"]) for r in rows] == [["m1", "m2"]]

    snapshot = await driver.snapshot_subgraph(config_id=config_id, build_id=None)
    (edge,) = snapshot["edges"]
    assert sorted(edge["evidence_refs"]) == ["m1", "m2"]
    # An N-1 restore reads this key from the snapshot, so it must carry it too.
    assert sorted(edge["evidence_msg_ids"]) == ["m1", "m2"]


async def test_the_old_key_wins_while_both_exist(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    # An N-1 writer refreshed only the old key after a backfill had copied it.
    await _legacy_edge(raw, config_id, ["m1"])
    async with raw.session() as session:
        await session.run(
            "MATCH ()-[r:REL {graphrag_config_id: $cid}]->() SET r.evidence_refs = ['stale']",
            cid=str(config_id),
        )
    rows = await driver.traverse(config_id=config_id, seed_entities=["alice"], hops=1)
    assert [r["evidence_refs"] for r in rows] == [["m1"]]


# ---------------------------------------------------------------------------
# Writes (AC-1)
# ---------------------------------------------------------------------------


async def test_a_delta_write_sets_both_keys_from_the_old_one(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    await _legacy_edge(raw, config_id, ["m1"])
    await driver.apply_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[
            Triple("alice", "knows", "bob", 0.8, ("m2",)),
            Triple("alice", "likes", "bob", 0.7, ("m3",)),
        ],
    )
    stored = await _raw_evidence(raw, config_id)
    assert _sorted(stored["knows"][0]) == _sorted(stored["knows"][1]) == ["m1", "m2"]
    assert stored["likes"][0] == stored["likes"][1] == ["m3"]


async def test_a_replace_write_sets_both_keys(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    await _legacy_edge(raw, config_id, ["docA"])
    await driver.replace_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[
            Triple("alice", "knows", "bob", 0.8, ("docB",)),
            Triple("alice", "knows", "bob", 0.9, ("docC",)),
        ],
    )
    stored = await _raw_evidence(raw, config_id)
    assert _sorted(stored["knows"][0]) == _sorted(stored["knows"][1]) == ["docB", "docC"]


# ---------------------------------------------------------------------------
# Snapshot restore across the version boundary (AC-3)
# ---------------------------------------------------------------------------


async def test_a_release_a_snapshot_restores_with_the_n_minus_1_cypher(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    await driver.apply_triples(
        config_id=config_id,
        project_id=_PROJECT_ID,
        build_id=uuid.uuid4(),
        triples=[Triple("alice", "knows", "bob", 0.9, ("m1", "m2"))],
    )
    snapshot = await driver.snapshot_subgraph(config_id=config_id, build_id=None)
    await driver.delete_all(config_id=config_id)

    async with raw.session() as session:
        await session.run(_N_MINUS_1_RESTORE_EDGES_CYPHER, rows=snapshot["edges"], cid=str(config_id))

    stored = await _raw_evidence(raw, config_id)
    assert _sorted(stored["knows"][0]) == ["m1", "m2"]


async def test_an_n_minus_1_snapshot_restores_with_release_a(
    driver: Neo4jAsyncDriver, raw: AsyncDriver, config_id: uuid.UUID
) -> None:
    # The shape release N-1 put into Redis: no evidence_refs key at all.
    snapshot = {
        "edges": [
            {
                "subject": "alice",
                "relation": "knows",
                "object": "bob",
                "confidence": 0.9,
                "evidence_msg_ids": ["m1", "m2"],
                "source_member_ids": [],
                "first_seen_at": None,
                "last_seen_at": None,
                "build_id": "b0",
            }
        ],
        "nodes": [],
    }
    await driver.restore_from_snapshot(config_id=config_id, snapshot=snapshot)

    stored = await _raw_evidence(raw, config_id)
    assert _sorted(stored["knows"][0]) == _sorted(stored["knows"][1]) == ["m1", "m2"]
    rows = await driver.traverse(config_id=config_id, seed_entities=["alice"], hops=1)
    assert [sorted(r["evidence_refs"]) for r in rows] == [["m1", "m2"]]


# ---------------------------------------------------------------------------
# Backfill command (AC-4)
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    # Own client, not get_redis(): see tests/integration/test_draft_store_ttl.py.
    from app.config.settings import get_settings

    client: Redis = Redis.from_url(get_settings().redis.dsn)
    try:
        yield client
    finally:
        await client.aclose()


async def test_the_backfill_counts_copies_and_is_idempotent(
    driver: Neo4jAsyncDriver,
    raw: AsyncDriver,
    redis: Redis,
    config_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smap.maintenance import neo4j_evidence_refs as cmd

    await _legacy_edge(raw, config_id, ["m1"], rel="knows")
    await _legacy_edge(raw, config_id, ["m2"], rel="likes")
    locks = RedisBuildLockStore(redis)

    monkeypatch.delenv(cmd.ARMED_ENV, raising=False)
    dry = await cmd.run(driver=driver, lock_store=locks, config_ids=[config_id], batch=1)
    assert (dry.dry_run, dry.pending, dry.copied, dry.remaining) == (True, 2, 0, 2)
    assert (await _raw_evidence(raw, config_id))["knows"] == (["m1"], None)

    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    armed = await cmd.run(driver=driver, lock_store=locks, config_ids=[config_id], batch=1)
    assert (armed.dry_run, armed.pending, armed.copied, armed.remaining, armed.busy) == (
        False,
        2,
        2,
        0,
        (),
    )
    stored = await _raw_evidence(raw, config_id)
    assert stored == {"knows": (["m1"], ["m1"]), "likes": (["m2"], ["m2"])}
    # The lock was released: a build could take it now.
    assert not await redis.exists(f"graphrag:lock:{config_id}")

    again = await cmd.run(driver=driver, lock_store=locks, config_ids=[config_id], batch=1)
    assert (again.pending, again.copied, again.remaining) == (0, 0, 0)


async def test_the_backfill_skips_a_busy_config(
    driver: Neo4jAsyncDriver,
    raw: AsyncDriver,
    redis: Redis,
    config_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smap.maintenance import neo4j_evidence_refs as cmd

    await _legacy_edge(raw, config_id, ["m1"])
    lock_key = f"graphrag:lock:{config_id}"
    await redis.set(lock_key, "a-build", ex=60)
    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    try:
        report = await cmd.run(driver=driver, lock_store=RedisBuildLockStore(redis), config_ids=[config_id])
    finally:
        held_by = await redis.get(lock_key)
        await redis.delete(lock_key)

    assert report.busy == (config_id,)
    assert (report.copied, report.remaining) == (0, 1)
    # Never broke or released the build's lock.
    assert held_by == b"a-build"
    assert (await _raw_evidence(raw, config_id))["knows"] == (["m1"], None)
