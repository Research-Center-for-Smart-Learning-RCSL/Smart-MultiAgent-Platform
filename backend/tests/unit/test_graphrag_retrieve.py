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
