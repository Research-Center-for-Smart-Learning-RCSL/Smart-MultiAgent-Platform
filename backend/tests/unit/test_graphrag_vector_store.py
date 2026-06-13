"""Unit tests for GraphRagVectorStore superseded-entity cleanup (DOM-8).

The GraphRAG entity collection accumulates across delta builds — each build
upserts only the entities in its own delta under a fresh ``build_id``.
``delete_superseded_entities`` must therefore drop *only* the prior-build
copies of the entity names a build re-embedded, never the still-live points of
entities an earlier build contributed.

These tests run the real :class:`GraphRagVectorStore` against an in-memory
Qdrant stand-in that *actually evaluates* the delete filter (must / must_not /
should), so a regression in the filter shape is caught — not just a regression
in which method is called.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)


def _cond_matches(cond: Any, payload: dict[str, Any]) -> bool:
    """A single ``FieldCondition`` (key == match.value) against a payload."""
    return payload.get(cond.key) == getattr(cond.match, "value", None)


def _filter_matches(flt: Any, payload: dict[str, Any]) -> bool:
    """Replicate Qdrant must / must_not / should (min_should=1) semantics."""
    if not all(_cond_matches(c, payload) for c in (flt.must or [])):
        return False
    if any(_cond_matches(c, payload) for c in (flt.must_not or [])):
        return False
    should = list(flt.should or [])
    if should and not any(_cond_matches(c, payload) for c in should):  # noqa: SIM103 (guard-clause chain)
        return False
    return True


class _FakeQdrant:
    """In-memory Qdrant stand-in that evaluates delete filters for real."""

    def __init__(self) -> None:
        # point_id (str) -> payload
        self.points: dict[str, dict[str, Any]] = {}
        self._collections: set[str] = set()

    async def collection_exists(self, name: str) -> bool:
        return name in self._collections

    async def upsert(self, *, collection_name: str, points: list[Any], wait: bool = True) -> None:
        self._collections.add(collection_name)
        for p in points:
            self.points[str(p.id)] = dict(p.payload or {})

    async def delete(self, *, collection_name: str, points_selector: Any, wait: bool = True) -> None:
        self.points = {
            pid: payload
            for pid, payload in self.points.items()
            if not _filter_matches(points_selector, payload)
        }


def _surviving(fake: _FakeQdrant) -> set[tuple[str, str, str]]:
    """The (config_id, entity, build_id) triples still present."""
    return {(p["config_id"], p["entity"], p["build_id"]) for p in fake.points.values()}


async def _seed(
    store: GraphRagVectorStore,
    *,
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    build_id: uuid.UUID,
    entities: list[str],
) -> None:
    await store.upsert_entities(
        project_id=project_id,
        config_id=config_id,
        build_id=build_id,
        points=[(uuid.uuid4(), [0.1, 0.2, 0.3], name, f"desc {name}") for name in entities],
    )


@pytest.mark.asyncio
async def test_supersede_keeps_untouched_and_live_entities() -> None:
    fake = _FakeQdrant()
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    other_config = uuid.uuid4()
    b1, b2, b_other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Initial build embeds alice/bob/carol; a later delta re-embeds alice and
    # introduces dave. A sibling config also has an entity called "alice".
    await _seed(
        store, project_id=project_id, config_id=config_id, build_id=b1, entities=["alice", "bob", "carol"]
    )
    await _seed(store, project_id=project_id, config_id=config_id, build_id=b2, entities=["alice", "dave"])
    await _seed(store, project_id=project_id, config_id=other_config, build_id=b_other, entities=["alice"])

    await store.delete_superseded_entities(
        project_id=project_id,
        config_id=config_id,
        keep_build_id=b2,
        entities=["alice", "dave"],
    )

    assert _surviving(fake) == {
        # alice's stale b1 copy is gone; only the live b2 copy remains.
        (str(config_id), "alice", str(b2)),
        # bob/carol were never re-embedded by b2 — still live from b1.
        (str(config_id), "bob", str(b1)),
        (str(config_id), "carol", str(b1)),
        # dave is new in b2.
        (str(config_id), "dave", str(b2)),
        # the sibling config's "alice" is untouched — config-scoped delete.
        (str(other_config), "alice", str(b_other)),
    }


@pytest.mark.asyncio
async def test_supersede_empty_entity_list_is_noop() -> None:
    """An empty list must NOT degrade into 'delete every other build'."""
    fake = _FakeQdrant()
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    b1 = uuid.uuid4()
    await _seed(store, project_id=project_id, config_id=config_id, build_id=b1, entities=["alice", "bob"])

    await store.delete_superseded_entities(
        project_id=project_id,
        config_id=config_id,
        keep_build_id=uuid.uuid4(),
        entities=[],
    )

    assert _surviving(fake) == {
        (str(config_id), "alice", str(b1)),
        (str(config_id), "bob", str(b1)),
    }


@pytest.mark.asyncio
async def test_supersede_missing_collection_is_noop() -> None:
    fake = _FakeQdrant()
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    # Nothing seeded → the collection does not exist; must not raise.
    await store.delete_superseded_entities(
        project_id=uuid.uuid4(),
        config_id=uuid.uuid4(),
        keep_build_id=uuid.uuid4(),
        entities=["alice"],
    )
    assert fake.points == {}
