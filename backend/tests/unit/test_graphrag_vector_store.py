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
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.knowledge.domain.errors import GraphRagCollectionDimensionMismatch
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


# ---------------------------------------------------------------------------
# D7 (AC-10) — idempotent collection create + dimension guard
# ---------------------------------------------------------------------------


class _CollFake:
    """Qdrant stand-in for ensure_graphrag_collection: tracks a collection's
    dimension and can simulate a concurrent create."""

    def __init__(
        self,
        *,
        existing_dim: int | None = None,
        create_conflicts: bool = False,
        appears_after_conflict_dim: int | None = None,
    ) -> None:
        self._dim = existing_dim
        self._create_conflicts = create_conflicts
        self._appears_after_conflict_dim = appears_after_conflict_dim
        self.created: list[tuple[str, int]] = []

    async def collection_exists(self, name: str) -> bool:
        return self._dim is not None

    async def get_collection(self, name: str) -> Any:
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=self._dim)))
        )

    async def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        if self._create_conflicts:
            # A racing writer created it first; surface the collection (possibly
            # at a different dimension) and raise "already exists".
            self._dim = self._appears_after_conflict_dim
            raise RuntimeError("collection already exists")
        self._dim = vectors_config.size
        self.created.append((collection_name, vectors_config.size))


@pytest.mark.asyncio
async def test_ensure_raises_on_dimension_mismatch() -> None:
    store = GraphRagVectorStore(_CollFake(existing_dim=1536))  # type: ignore[arg-type]
    with pytest.raises(GraphRagCollectionDimensionMismatch):
        await store.ensure_graphrag_collection(uuid.uuid4(), vector_size=768)


@pytest.mark.asyncio
async def test_ensure_matching_dimension_is_noop() -> None:
    fake = _CollFake(existing_dim=1536)
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    await store.ensure_graphrag_collection(uuid.uuid4(), vector_size=1536)
    assert fake.created == []  # already exists at the right size → no re-create


@pytest.mark.asyncio
async def test_ensure_creates_when_absent() -> None:
    fake = _CollFake(existing_dim=None)
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    await store.ensure_graphrag_collection(project_id, vector_size=1024)
    assert fake.created == [(f"graphrag_{str(project_id).replace('-', '_')}", 1024)]


@pytest.mark.asyncio
async def test_ensure_tolerates_concurrent_create_same_dim() -> None:
    # Create loses the race but the winner made the collection at the same size:
    # tolerated, no raise.
    fake = _CollFake(existing_dim=None, create_conflicts=True, appears_after_conflict_dim=1024)
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    await store.ensure_graphrag_collection(uuid.uuid4(), vector_size=1024)


@pytest.mark.asyncio
async def test_ensure_concurrent_create_conflicting_dim_raises() -> None:
    # Create loses the race and the winner made it at a different size → the
    # dimension guard still fires.
    fake = _CollFake(existing_dim=None, create_conflicts=True, appears_after_conflict_dim=768)
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    with pytest.raises(GraphRagCollectionDimensionMismatch):
        await store.ensure_graphrag_collection(uuid.uuid4(), vector_size=1024)


@pytest.mark.asyncio
async def test_ensure_reraises_genuine_create_failure() -> None:
    # Create fails and the collection is still absent → a real failure, re-raised.
    fake = _CollFake(existing_dim=None, create_conflicts=True, appears_after_conflict_dim=None)
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await store.ensure_graphrag_collection(uuid.uuid4(), vector_size=1024)


@pytest.mark.asyncio
async def test_collection_prefix_routes_to_distinct_collection() -> None:
    """A non-default prefix routes collection ops to a differently-named
    collection so a Knowledge Map (``knowmap_{project_id}``) never collides with
    the Concept Map's default ``graphrag_{project_id}`` collection."""
    project_id = uuid.uuid4()
    pid_slug = str(project_id).replace("-", "_")
    point = (uuid.uuid4(), [0.1, 0.2], "alice", "desc")

    default = _FakeQdrant()
    await GraphRagVectorStore(default).upsert_entities(  # type: ignore[arg-type]
        project_id=project_id,
        config_id=uuid.uuid4(),
        build_id=uuid.uuid4(),
        points=[point],
    )
    assert default._collections == {f"graphrag_{pid_slug}"}

    knowmap = _FakeQdrant()
    await GraphRagVectorStore(knowmap, prefix="knowmap").upsert_entities(  # type: ignore[arg-type]
        project_id=project_id,
        config_id=uuid.uuid4(),
        build_id=uuid.uuid4(),
        points=[point],
    )
    assert knowmap._collections == {f"knowmap_{pid_slug}"}
