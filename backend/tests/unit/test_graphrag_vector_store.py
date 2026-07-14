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
    _SCROLL_PAGE,
    GraphRagVectorStore,
    graphrag_collection_name,
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


@pytest.mark.asyncio
async def test_delete_points_not_in_build_removes_orphans_keeps_current() -> None:
    # F-6 (§8.3 / AC-4): the build-scoped sweep removes EVERY prior-build point for
    # the config — including entities the current corpus no longer produces (which
    # the name-scoped supersede can never reach) — while keeping the current build
    # and other configs.
    fake = _FakeQdrant()
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    other_config = uuid.uuid4()
    b1, b2, b_other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _seed(
        store, project_id=project_id, config_id=config_id, build_id=b1, entities=["alice", "bob", "carol"]
    )
    await _seed(store, project_id=project_id, config_id=config_id, build_id=b2, entities=["alice", "dave"])
    await _seed(store, project_id=project_id, config_id=other_config, build_id=b_other, entities=["alice"])

    await store.delete_points_not_in_build(project_id=project_id, config_id=config_id, keep_build_id=b2)

    assert _surviving(fake) == {
        # Only the current build's config points survive — bob/carol (gone from the
        # corpus) and alice's stale b1 copy are all removed, no entity list needed.
        (str(config_id), "alice", str(b2)),
        (str(config_id), "dave", str(b2)),
        # The sibling config is config-scoped-untouched.
        (str(other_config), "alice", str(b_other)),
    }


@pytest.mark.asyncio
async def test_delete_points_not_in_build_missing_collection_is_noop() -> None:
    fake = _FakeQdrant()
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]
    await store.delete_points_not_in_build(
        project_id=uuid.uuid4(), config_id=uuid.uuid4(), keep_build_id=uuid.uuid4()
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


# ---------------------------------------------------------------------------
# F-8 — Qdrant-side config-id enumeration for the orphan sweep.
# ---------------------------------------------------------------------------


class _ScrollFake:
    """Qdrant stand-in for enumeration: named collections of payloads, a paged
    ``scroll`` that honors the caller's limit/offset, and ``get_collections``."""

    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        self._collections = collections
        self.scroll_calls = 0

    async def collection_exists(self, name: str) -> bool:
        return name in self._collections

    async def get_collections(self) -> Any:
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self._collections])

    async def scroll(
        self,
        *,
        collection_name: str,
        with_payload: Any,
        with_vectors: Any,
        limit: int,
        offset: Any = None,
    ) -> tuple[list[Any], Any]:
        self.scroll_calls += 1
        payloads = self._collections.get(collection_name, [])
        start = int(offset or 0)
        page = payloads[start : start + limit]
        nxt = start + limit if start + limit < len(payloads) else None
        records = [SimpleNamespace(id=str(uuid.uuid4()), payload=p) for p in page]
        return records, nxt


@pytest.mark.asyncio
async def test_list_config_ids_collects_distinct_across_scroll_pages() -> None:
    # AC-5: distinct config_ids for one project's collection, paged to completion.
    project_id = uuid.uuid4()
    name = graphrag_collection_name(project_id)
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    # More points than a single scroll page, alternating two configs + a malformed row.
    payloads: list[dict[str, Any]] = [
        {"config_id": str(c1 if i % 2 == 0 else c2)} for i in range(_SCROLL_PAGE + 10)
    ]
    payloads.append({"config_id": "not-a-uuid"})  # unparseable → skipped, not raised
    fake = _ScrollFake({name: payloads})
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type]

    result = await store.list_config_ids(project_id)

    assert result == {c1, c2}
    assert fake.scroll_calls >= 2  # genuinely paged


@pytest.mark.asyncio
async def test_list_config_ids_missing_collection_is_empty() -> None:
    store = GraphRagVectorStore(_ScrollFake({}))  # type: ignore[arg-type]
    assert await store.list_config_ids(uuid.uuid4()) == set()


@pytest.mark.asyncio
async def test_list_all_config_ids_spans_collections_and_skips_foreign_prefix() -> None:
    # The cross-collection variant the sweep uses: enumerate every graphrag_* collection,
    # yield (config_id, project_id), and ignore a knowmap_* / unrelated collection.
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    fake = _ScrollFake(
        {
            graphrag_collection_name(p1): [{"config_id": str(c1)}],
            graphrag_collection_name(p2): [{"config_id": str(c2)}],
            graphrag_collection_name(uuid.uuid4(), prefix="knowmap"): [{"config_id": str(uuid.uuid4())}],
            "some_other_collection": [{"config_id": str(uuid.uuid4())}],
        }
    )
    store = GraphRagVectorStore(fake)  # type: ignore[arg-type] # default graphrag prefix

    result = await store.list_all_config_ids()

    assert result == {(c1, p1), (c2, p2)}


@pytest.mark.asyncio
async def test_list_all_config_ids_knowmap_prefix_enumerates_knowmap_only() -> None:
    # Cross-prefix symmetry: a knowmap-prefixed store enumerates knowmap_* collections
    # and skips graphrag_*, so the two sweep consumers together cover both products
    # without either leaking the other's orphans.
    p, c = uuid.uuid4(), uuid.uuid4()
    fake = _ScrollFake(
        {
            graphrag_collection_name(p, prefix="knowmap"): [{"config_id": str(c)}],
            graphrag_collection_name(uuid.uuid4()): [{"config_id": str(uuid.uuid4())}],  # graphrag → skipped
        }
    )
    store = GraphRagVectorStore(fake, prefix="knowmap")  # type: ignore[arg-type]

    assert await store.list_all_config_ids() == {(c, p)}
