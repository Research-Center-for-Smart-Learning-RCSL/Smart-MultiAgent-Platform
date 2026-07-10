"""Unit tests for :class:`GraphRagRetrieveService` (E.8 / R11.06)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_retrieve import (
    GraphRagRetrieveService,
)
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


class FakeRepo:
    def __init__(self, cfg: GraphRagConfig) -> None:
        self._cfg = cfg

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False):
        return self._cfg


class FakeVectors:
    def __init__(self, hits: list[Any]) -> None:
        self.hits = hits

    async def search_entities(self, **_: Any):
        return self.hits


class FakeNeo4j:
    def __init__(self, edges: list[dict[str, Any]]) -> None:
        self.edges = edges
        self.traverse_calls: list[tuple[list[str], int]] = []

    async def traverse(self, *, config_id, seed_entities, hops):
        self.traverse_calls.append((list(seed_entities), hops))
        return list(self.edges)

    async def snapshot_subgraph(self, **_: Any):
        return {"edges": []}

    async def apply_triples(self, **_: Any):
        return 0

    async def delete_by_build(self, **_: Any) -> None:
        return None

    async def delete_all(self, **_: Any) -> None:
        return None

    async def restore_from_snapshot(self, **_: Any) -> None:
        return None


class FakeEmbedder:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 3 for _ in texts]


async def _factory(cfg):
    return FakeEmbedder()


def _cfg() -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=BuildState.IDLE,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_hybrid_query_returns_bundle() -> None:
    cfg = _cfg()
    msg_id = uuid.uuid4()

    class _Hit:
        def __init__(self, entity: str) -> None:
            self.point_id = uuid.uuid4()
            self.score = 0.9
            self.entity = entity
            self.description = f"desc {entity}"
            self.build_id = None

    vectors = FakeVectors([_Hit("alice"), _Hit("bob")])
    neo4j = FakeNeo4j(
        edges=[
            {
                "subject": "alice",
                "relation": "knows",
                "object": "bob",
                "confidence": 0.9,
                "evidence_msg_ids": [str(msg_id)],
            },
        ],
    )
    seen_agent_ids: list[uuid.UUID | None] = []

    async def _recording_fetcher(ids: list[str], querying_agent_id: uuid.UUID | None) -> list[str]:
        seen_agent_ids.append(querying_agent_id)
        return [f"excerpt-{i}" for i in range(len(ids))]

    service = GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        embedder_factory=_factory,
        configs=FakeRepo(cfg),  # type: ignore[arg-type]
        evidence_fetcher=_recording_fetcher,
    )

    agent_id = uuid.uuid4()
    bundle = await service.query(
        config_id=cfg.id,
        text="who knows bob?",
        top_k=5,
        hops=2,
        querying_agent_id=agent_id,
    )

    assert bundle.entities == ("alice", "bob")
    assert len(bundle.relations) == 1
    rel = bundle.relations[0]
    assert rel.subject == "alice"
    assert rel.object == "bob"
    assert rel.evidence_refs == (str(msg_id),)
    # The service must forward the querying agent to the fetcher unchanged, so
    # the room-ACL gate (AC-7) authorizes against the right principal.
    assert seen_agent_ids == [agent_id]
    assert bundle.evidence_excerpts == ("excerpt-0",)
    assert neo4j.traverse_calls == [(["alice", "bob"], 2)]


def test_recency_weighted_score_recent_beats_stale() -> None:
    """WS5 AC-9 core: under a short half-life a recent low-confidence edge
    outranks a stale high-confidence one; NULL inputs coalesce safely."""
    from contexts.knowledge.domain.graphrag import recency_weighted_score

    now = 1_000_000.0
    day = 86_400.0
    stale_strong = recency_weighted_score(
        confidence=0.9, last_seen_at=now - 100 * day, half_life_days=7, now=now
    )
    recent_weak = recency_weighted_score(
        confidence=0.5, last_seen_at=now - 1 * day, half_life_days=7, now=now
    )
    assert recent_weak > stale_strong
    # No timestamp or no half-life -> pure confidence (decay factor 1.0).
    assert recency_weighted_score(confidence=0.8, last_seen_at=None, half_life_days=7, now=now) == 0.8
    assert recency_weighted_score(confidence=0.8, last_seen_at=now - day, half_life_days=None, now=now) == 0.8
    # NULL confidence coalesces to 0.0, like the traversal ordering already does.
    assert recency_weighted_score(confidence=None, last_seen_at=now, half_life_days=7, now=now) == 0.0
    # The numeric half-life point: at age == half_life_days, the decay factor
    # must be exactly 0.5, not exp(-1) ~ 0.368 (a missing ln(2) factor would
    # make the true half-life ~0.693x the configured value).
    at_half_life = recency_weighted_score(
        confidence=1.0, last_seen_at=now - 7 * day, half_life_days=7, now=now
    )
    assert at_half_life == pytest.approx(0.5, rel=1e-9)


@pytest.mark.asyncio
async def test_retrieve_reranks_recent_over_stale() -> None:
    """WS5 AC-9 end-to-end: the retrieve service applies recency decay so a
    recent weak edge sorts above a stale strong edge under a short half-life."""
    import time

    cfg = _cfg()
    day = 86_400.0
    now_epoch = time.time()

    class _Hit:
        def __init__(self, entity: str) -> None:
            self.point_id = uuid.uuid4()
            self.score = 0.9
            self.entity = entity
            self.description = f"desc {entity}"
            self.build_id = None

    vectors = FakeVectors([_Hit("alice"), _Hit("bob")])
    neo4j = FakeNeo4j(
        edges=[
            {
                "subject": "alice",
                "relation": "knew",
                "object": "x",
                "confidence": 0.9,  # strong but 100 days stale
                "evidence_msg_ids": [],
                "last_seen_at": now_epoch - 100 * day,
            },
            {
                "subject": "bob",
                "relation": "knows",
                "object": "y",
                "confidence": 0.4,  # weak but 1 day fresh
                "evidence_msg_ids": [],
                "last_seen_at": now_epoch - 1 * day,
            },
        ],
    )
    service = GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        embedder_factory=_factory,
        configs=FakeRepo(cfg),  # type: ignore[arg-type]
        default_half_life_days=7.0,
    )

    bundle = await service.query(config_id=cfg.id, text="who?")

    assert [r.relation for r in bundle.relations] == ["knows", "knew"]


def _room_message(chatroom_id: uuid.UUID):
    from contexts.conversation.domain.models import Message, SenderType

    return Message(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        sender_type=SenderType.USER,
        sender_id=uuid.uuid4(),
        content_md="  Alice\n\nconfirmed   the roadmap milestone.  ",
    )


@pytest.mark.asyncio
async def test_context_provider_fetches_message_evidence_excerpts() -> None:
    from contexts.knowledge.application.graphrag_context_provider import build_evidence_fetcher

    room_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    msg = _room_message(room_id)
    message_id = msg.id
    missing_id = uuid.uuid4()
    messages = {message_id: msg}

    async def get_message(mid: uuid.UUID):
        return messages.get(mid)

    async def is_agent_in_chatroom(*, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        return chatroom_id == room_id

    fetcher = build_evidence_fetcher(get_message, is_agent_in_chatroom)
    excerpts = await fetcher([str(message_id), str(missing_id), str(message_id)], agent_id)

    assert excerpts == ["user: Alice confirmed the roadmap milestone."]


@pytest.mark.asyncio
async def test_evidence_fetcher_drops_excerpts_from_unreadable_rooms() -> None:
    """WS3 AC-7: an agent gets no excerpt from a room it does not participate in."""
    from contexts.knowledge.application.graphrag_context_provider import build_evidence_fetcher

    readable = _room_message(uuid.uuid4())
    hidden = _room_message(uuid.uuid4())
    messages = {readable.id: readable, hidden.id: hidden}
    agent_id = uuid.uuid4()

    async def get_message(mid: uuid.UUID):
        return messages.get(mid)

    async def is_agent_in_chatroom(*, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        return chatroom_id == readable.chatroom_id

    fetcher = build_evidence_fetcher(get_message, is_agent_in_chatroom)
    excerpts = await fetcher([str(readable.id), str(hidden.id)], agent_id)

    assert excerpts == ["user: Alice confirmed the roadmap milestone."]


@pytest.mark.asyncio
async def test_evidence_fetcher_fails_closed_without_querying_agent() -> None:
    """WS3 AC-7: with no querying principal the fetcher returns nothing."""
    from contexts.knowledge.application.graphrag_context_provider import build_evidence_fetcher

    msg = _room_message(uuid.uuid4())

    async def get_message(mid: uuid.UUID):
        return msg

    async def is_agent_in_chatroom(*, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        return True

    fetcher = build_evidence_fetcher(get_message, is_agent_in_chatroom)
    excerpts = await fetcher([str(msg.id)], None)

    assert excerpts == []


@pytest.mark.asyncio
async def test_evidence_fetcher_fills_past_unreadable_refs_and_memoizes() -> None:
    """WS3 AC-7 fix: the ACL filter runs before the cap, so leading unreadable
    refs do not starve readable ones, and membership is memoized per room."""
    from contexts.knowledge.application.graphrag_context_provider import (
        _MAX_EVIDENCE_EXCERPTS,
        build_evidence_fetcher,
    )

    hidden_room = uuid.uuid4()
    readable_room = uuid.uuid4()
    # 12 unreadable refs (same hidden room) followed by 12 readable refs.
    hidden = [_room_message(hidden_room) for _ in range(12)]
    readable = [_room_message(readable_room) for _ in range(12)]
    messages = {m.id: m for m in hidden + readable}
    agent_id = uuid.uuid4()

    membership_calls: list[uuid.UUID] = []

    async def get_message(mid: uuid.UUID):
        return messages.get(mid)

    async def is_agent_in_chatroom(*, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        membership_calls.append(chatroom_id)
        return chatroom_id == readable_room

    fetcher = build_evidence_fetcher(get_message, is_agent_in_chatroom)
    refs = [str(m.id) for m in hidden + readable]
    excerpts = await fetcher(refs, agent_id)

    # Cap is filled with readable excerpts despite 12 leading unreadable refs.
    assert len(excerpts) == _MAX_EVIDENCE_EXCERPTS
    assert all(e == "user: Alice confirmed the roadmap milestone." for e in excerpts)
    # Memoization: one ACL query per distinct room, not per ref.
    assert membership_calls.count(hidden_room) == 1
    assert membership_calls.count(readable_room) == 1


@pytest.mark.asyncio
async def test_context_provider_merges_multi_query_bundles() -> None:
    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider
    from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge

    def _bundle_for(query: str) -> GraphRagBundle:
        if query == "first":
            return GraphRagBundle(
                entities=("alice",),
                relations=(
                    RelationEdge(
                        subject="alice",
                        relation="owns",
                        object="roadmap",
                        confidence=0.7,
                        evidence_refs=(),
                    ),
                ),
                evidence_excerpts=("excerpt A",),
            )
        return GraphRagBundle(
            entities=("roadmap",),
            relations=(
                RelationEdge(
                    subject="roadmap",
                    relation="targets",
                    object="q3",
                    confidence=0.9,
                    evidence_refs=(),
                ),
            ),
            evidence_excerpts=("excerpt B",),
        )

    class _Provider(GraphRagContextProvider):
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def _graphrag_query(self, config_id: uuid.UUID, queries, querying_agent_id=None):
            self.queries.extend(queries)
            return [_bundle_for(q) for q in queries]

    provider = _Provider()
    text = await provider.query(graphrag_config_id=uuid.uuid4(), query_texts=["first", "second"])

    assert provider.queries == ["first", "second"]
    assert text is not None
    assert "alice" in text
    assert "roadmap" in text
    assert "targets" in text


def _layer_provider(bundles_by_config):
    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider

    class _Provider(GraphRagContextProvider):
        def __init__(self) -> None:
            self.order: list[uuid.UUID] = []

        async def _graphrag_query(self, config_id: uuid.UUID, queries, querying_agent_id=None):
            self.order.append(config_id)
            bundle = bundles_by_config.get(config_id)
            return [bundle] if bundle is not None else []

    return _Provider()


@pytest.mark.asyncio
async def test_query_layers_tiered_fill_and_narrowest_dedup() -> None:
    """WS4 AC-4: narrow->wide fill order, entity/relation dedup keeps narrowest."""
    from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge

    chatroom_id, group_id, workspace_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    bundles = {
        chatroom_id: GraphRagBundle(
            entities=("alice",),
            relations=(RelationEdge("alice", "owns", "roadmap", 0.90, ()),),
            evidence_excerpts=("chatroom-ev",),
        ),
        group_id: GraphRagBundle(
            entities=("alice", "bob"),  # alice repeats -> narrowest (chatroom) kept
            relations=(
                RelationEdge("alice", "owns", "roadmap", 0.99, ()),  # dup key -> narrowest kept
                RelationEdge("bob", "knows", "carol", 0.80, ()),
            ),
            evidence_excerpts=("group-ev",),
        ),
        workspace_id: GraphRagBundle(
            entities=("dave",),
            relations=(RelationEdge("dave", "leads", "team", 0.70, ()),),
            evidence_excerpts=("workspace-ev",),
        ),
    }
    provider = _layer_provider(bundles)
    text = await provider.query_layers(
        graphrag_config_ids=[chatroom_id, group_id, workspace_id],
        query_texts=["q"],
        querying_agent_id=uuid.uuid4(),
    )

    assert text is not None
    assert provider.order == [chatroom_id, group_id, workspace_id]
    ent_line = next(line for line in text.splitlines() if line.startswith("entities:"))
    assert ent_line == "entities: alice, bob, dave"
    # Narrow-first ordering preserved (no cross-layer re-sort).
    assert text.index("(alice) -[owns") < text.index("(bob) -[knows") < text.index("(dave) -[leads")
    # The duplicate relation appears once, at the narrowest layer's confidence.
    assert text.count("(alice) -[owns") == 1
    assert "(alice) -[owns c=0.90]" in text


@pytest.mark.asyncio
async def test_query_layers_single_layer_matches_query() -> None:
    """WS4 AC-5: a single-layer assembly is byte-identical to the flat query."""
    from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge

    cid = uuid.uuid4()
    bundles = {
        cid: GraphRagBundle(
            entities=("alice", "bob"),
            relations=(
                RelationEdge("alice", "owns", "roadmap", 0.70, ()),
                RelationEdge("bob", "knows", "carol", 0.90, ()),
            ),
            evidence_excerpts=("ev-1", "ev-2"),
        )
    }
    layered = await _layer_provider(bundles).query_layers(graphrag_config_ids=[cid], query_texts=["q"])
    flat = await _layer_provider(bundles).query(graphrag_config_id=cid, query_texts=["q"])

    assert layered == flat
    assert layered is not None


@pytest.mark.asyncio
async def test_query_layers_isolates_a_failing_layer() -> None:
    """WS4: a layer whose retrieval raises degrades to fewer layers, not zero."""
    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider
    from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge

    ok_id, bad_id = uuid.uuid4(), uuid.uuid4()

    class _Provider(GraphRagContextProvider):
        def __init__(self) -> None:
            pass

        async def _graphrag_query(self, config_id: uuid.UUID, queries, querying_agent_id=None):
            if config_id == bad_id:
                raise RuntimeError("neo4j down")
            return [
                GraphRagBundle(
                    entities=("alice",),
                    relations=(RelationEdge("alice", "owns", "roadmap", 0.9, ()),),
                    evidence_excerpts=(),
                )
            ]

    text = await _Provider().query_layers(
        graphrag_config_ids=[ok_id, bad_id], query_texts=["q"], querying_agent_id=uuid.uuid4()
    )

    assert text is not None
    assert "alice" in text


@pytest.mark.asyncio
async def test_turn_clients_are_built_once_and_reused_across_layers() -> None:
    """WS4/perf regression: query_layers must build the Neo4j driver + Qdrant
    client ONCE per turn and reuse them across every layer, not rebuild a
    connection per layer (each rebuild pays a connect/auth handshake on the
    hot per-message path)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider

    driver_build_count = 0
    qclient_build_count = 0

    class _FakeDriver:
        def __init__(self, **_: Any) -> None:
            nonlocal driver_build_count
            driver_build_count += 1
            self.close = AsyncMock()

    class _FakeQClient:
        def __init__(self, **_: Any) -> None:
            nonlocal qclient_build_count
            qclient_build_count += 1
            self.close = AsyncMock()

    fake_settings = MagicMock()
    fake_settings.neo4j = MagicMock(url="bolt://fake", user="u", password="p")

    provider = GraphRagContextProvider(None, router=None, qdrant_url="http://fake:6333")  # type: ignore[arg-type]

    with (
        patch("app.config.settings.get_settings", return_value=fake_settings),
        patch("contexts.knowledge.infrastructure.neo4j_driver.Neo4jAsyncDriver", _FakeDriver),
        patch("qdrant_client.AsyncQdrantClient", _FakeQClient),
    ):
        first = await provider._get_turn_clients()
        second = await provider._get_turn_clients()
        third = await provider._get_turn_clients()

        # Same pair returned every time within the turn — only built once.
        assert first is second is third
        assert driver_build_count == 1
        assert qclient_build_count == 1

        await provider._close_turn_clients()
        driver, qclient = first
        driver.close.assert_awaited_once()
        qclient.close.assert_awaited_once()
        assert provider._turn_clients is None

        # After a close, the cache is genuinely cleared — a later call (as a
        # fresh turn would make, since TurnEngine and this provider are rebuilt
        # per turn in production) builds a new pair rather than reusing a
        # closed one.
        fresh = await provider._get_turn_clients()
        assert driver_build_count == 2
        assert qclient_build_count == 2
        assert fresh is not first


@pytest.mark.asyncio
async def test_empty_vector_hits_returns_empty_bundle() -> None:
    cfg = _cfg()
    service = GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=FakeNeo4j(edges=[]),
        vector_store=FakeVectors([]),  # type: ignore[arg-type]
        embedder_factory=_factory,
        configs=FakeRepo(cfg),  # type: ignore[arg-type]
    )

    bundle = await service.query(config_id=cfg.id, text="empty")
    assert bundle.entities == ()
    assert bundle.relations == ()


def test_bundle_serialises_under_2kb_cap() -> None:
    from contexts.knowledge.domain.graphrag import (
        GraphRagBundle,
        RelationEdge,
    )

    huge = "x" * 4000
    bundle = GraphRagBundle(
        entities=("a", "b"),
        relations=(
            RelationEdge(
                subject="a",
                relation="r",
                object="b",
                confidence=1.0,
                evidence_refs=(),
            ),
        ),
        evidence_excerpts=(huge,),
    )
    payload = bundle.as_system_message()
    import json

    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= 2048
    assert payload["metadata"]["type"] == "graphrag"
