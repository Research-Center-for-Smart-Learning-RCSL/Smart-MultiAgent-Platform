"""F-16 / F-17 — whole-request token budgeting for turn assembly.

Covers the pieces the room path wires together:
- ``_assemble_agent_knowledge`` distributes per-source budgets and omits a
  zero-budget source (F-16 allocator wiring).
- ``_assemble_history`` decides compaction against the *assembled* request
  (history + non-knowledge prefix), not history alone (F-17).
- ``_knowledge_starved`` / ``_has_knowledge_source`` — a floored budget drops
  every knowledge block, so it fails the turn loudly instead of silently
  answering from nothing (AC-11).

The pure budget helper and allocator, and each provider's block truncation, are
unit-tested in ``test_context_compaction.py`` / ``test_graphrag_retrieve.py`` /
``test_rag_services.py``; here we pin the turn-engine glue.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.context import KnowledgeBudget
from contexts.keys.domain.providers import ApiKeyProvider
from tests.unit.turn_engine_fakes import PublisherSpy


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f


class _RagCtx:
    def __init__(self, block: str) -> None:
        self.block = block
        self.sources: list = []


def _agent():
    return SimpleNamespace(id=uuid.uuid4(), rag_config_id=uuid.uuid4(), knowmap_config_id=uuid.uuid4())


# --------------------------------------------------------------------------- #
# _assemble_agent_knowledge budget distribution (F-16)
# --------------------------------------------------------------------------- #


def _wire_providers(engine, *, rag="RAG", concept="CONCEPT", knowmap="KNOWMAP", seen=None):
    async def _rag(agent, queries, *, token_budget=None):
        if seen is not None:
            seen["rag"] = token_budget
        return _RagCtx(rag) if rag is not None else None

    async def _graph(agent, chatroom_id, queries, *, token_budget=None):
        if seen is not None:
            seen["concept"] = token_budget
        return concept

    async def _km(agent, queries, *, token_budget=None):
        if seen is not None:
            seen["knowmap"] = token_budget
        return knowmap

    engine._rag_context = _rag  # type: ignore[attr-defined]
    engine._graphrag_context = _graph  # type: ignore[attr-defined]
    engine._knowmap_context = _km  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_assemble_knowledge_graph_sources_capped_file_rag_takes_remainder() -> None:
    # Graph blocks draw first, each capped at graph_source_cap; File RAG receives
    # the remainder of total measured from what the graph blocks actually rendered.
    engine = te.TurnEngine.__new__(te.TurnEngine)
    seen: dict = {}
    _wire_providers(engine, concept="CONCEPT", knowmap="KNOWMAP", seen=seen)

    budget = KnowledgeBudget(total=5000, graph_source_cap=700)
    blocks, _rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budget=budget
    )

    assert blocks == ["RAG", "CONCEPT", "KNOWMAP"]
    assert seen["concept"] == 700  # min(cap, remaining=5000)
    assert seen["knowmap"] == 700  # min(cap, remaining after concept)
    # File RAG reclaims all but the graph blocks' *actual* rendered cost.
    expected_rag = 5000 - te.tx.estimate_tokens("CONCEPT") - te.tx.estimate_tokens("KNOWMAP")
    assert seen["rag"] == expected_rag


@pytest.mark.asyncio
async def test_assemble_knowledge_reclaims_absent_graph_budget_for_file_rag() -> None:
    # Fix #1: an absent Concept Map must NOT strand its graph_source_cap — File RAG
    # reclaims it instead of being capped at total - 2*cap.
    engine = te.TurnEngine.__new__(te.TurnEngine)
    seen: dict = {}
    _wire_providers(engine, concept=None, knowmap="KNOWMAP", seen=seen)

    budget = KnowledgeBudget(total=5000, graph_source_cap=700)
    blocks, _rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budget=budget
    )

    assert blocks == ["RAG", "KNOWMAP"]  # no Concept Map block
    # Concept reservation reclaimed: File RAG gets total - knowmap cost, NOT 3600.
    expected_rag = 5000 - te.tx.estimate_tokens("KNOWMAP")
    assert seen["rag"] == expected_rag


@pytest.mark.asyncio
async def test_assemble_knowledge_omits_file_rag_when_graph_blocks_exhaust_budget() -> None:
    # When the graph blocks' actual rendered cost consumes the whole budget, File
    # RAG is left zero and omitted entirely (never queried).
    engine = te.TurnEngine.__new__(te.TurnEngine)
    seen: dict = {}
    # Latin len//4: 2800 chars -> 700 tokens, 1600 chars -> 400 tokens.
    _wire_providers(engine, concept="x" * 2800, knowmap="y" * 1600, seen=seen)

    budget = KnowledgeBudget(total=1000, graph_source_cap=700)
    blocks, rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budget=budget
    )

    assert "rag" not in seen  # remaining hit zero -> File RAG never queried
    assert rag_ctx is None
    assert blocks == ["x" * 2800, "y" * 1600]


@pytest.mark.asyncio
async def test_assemble_knowledge_zero_total_omits_everything() -> None:
    engine = te.TurnEngine.__new__(te.TurnEngine)
    seen: dict = {}
    _wire_providers(engine, seen=seen)

    budget = KnowledgeBudget(total=0, graph_source_cap=700)
    blocks, rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budget=budget
    )

    assert seen == {}  # nothing queried
    assert rag_ctx is None
    assert blocks == []


# --------------------------------------------------------------------------- #
# _assemble_history compaction decision (F-17)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_assemble_history_budgets_the_next_request_not_history_alone(monkeypatch) -> None:
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]

    history = [
        SimpleNamespace(role="user", content="x", token_count=500, id=uuid.uuid4(), sender_id=uuid.uuid4())
    ]
    monkeypatch.setattr(te.tx, "load_model_history", _async_return(history))

    async def _no_flag(_cid, _agent):
        return False

    engine._consume_compact_flag = _no_flag  # type: ignore[attr-defined]

    seen: dict = {}

    def _spy_should(*, mode, projected_tokens, context_token_cap, provider_context_limit):
        seen["projected"] = projected_tokens
        return False  # short-circuit before the lock/compaction machinery

    monkeypatch.setattr(te.ctxmod, "should_compact", _spy_should)

    agent = SimpleNamespace(
        id=uuid.uuid4(),
        context_mode=SimpleNamespace(value="compact"),
        context_token_cap=1000,
    )
    out = await engine._assemble_history(
        agent, uuid.uuid4(), 128_000, ApiKeyProvider.CLAUDE, "claude-opus-4-8", extra_projected_tokens=800
    )

    # F-17: the compaction decision sees history (500) + the non-knowledge
    # prefix (800), not history alone — so a large prompt/tool prefix can trigger
    # compaction even when stored history is well under the cap.
    assert seen["projected"] == 1300
    assert out is history


# --------------------------------------------------------------------------- #
# AC-11 — a floored knowledge budget is loud, not silent
# --------------------------------------------------------------------------- #


class _Savepoint:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        self._session.savepoints += 1
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


class _Session:
    """Stands in for the AsyncSession, recording SAVEPOINT use.

    The Concept Map lookup must run under ``begin_nested()``: a DB fault there
    otherwise aborts the turn's whole transaction, including the pending
    ``agent.turn_started`` audit insert.
    """

    def __init__(self) -> None:
        self.savepoints = 0

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)


def _engine_with_layers(layers):
    """Engine stub whose Concept Map layer lookup returns *layers* (or raises it)."""
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _Session()  # type: ignore[attr-defined]

    class _Facade:
        def __init__(self, _db) -> None:
            pass

        async def resolve_graphrag_layers(self, *, agent_id, chatroom_id):
            if isinstance(layers, Exception):
                raise layers
            return layers

    return engine, _Facade


def _patch_facade(monkeypatch, facade_cls) -> None:
    import contexts.knowledge.interfaces.facade as kfacade

    monkeypatch.setattr(kfacade, "KnowledgeFacade", facade_cls)


def _bare_agent(**overrides):
    base = {"id": uuid.uuid4(), "rag_config_id": None, "knowmap_config_id": None}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_file_rag_binding_short_circuits_before_any_lookup(monkeypatch) -> None:
    # The two per-Agent bindings are visible on the row, so they must answer
    # without paying for the room-scoped Concept Map query.
    engine, facade_cls = _engine_with_layers(AssertionError("must not be consulted"))
    _patch_facade(monkeypatch, facade_cls)

    assert await engine._has_knowledge_source(_bare_agent(rag_config_id=uuid.uuid4()), uuid.uuid4()) is True
    assert engine._db.savepoints == 0


@pytest.mark.asyncio
async def test_knowledge_map_binding_short_circuits_before_any_lookup(monkeypatch) -> None:
    engine, facade_cls = _engine_with_layers(AssertionError("must not be consulted"))
    _patch_facade(monkeypatch, facade_cls)

    agent = _bare_agent(knowmap_config_id=uuid.uuid4())

    assert await engine._has_knowledge_source(agent, uuid.uuid4()) is True
    assert engine._db.savepoints == 0


@pytest.mark.asyncio
async def test_a_concept_map_covering_the_room_counts_as_a_source(monkeypatch) -> None:
    # The Concept Map is room-scoped: it is not visible on the Agent row, so a
    # binding-only check would miss an agent whose sole source is a layer.
    engine, facade_cls = _engine_with_layers(["layer-1"])
    _patch_facade(monkeypatch, facade_cls)

    assert await engine._has_knowledge_source(_bare_agent(), uuid.uuid4()) is True
    # The lookup runs inside the turn's live transaction, so it must be isolated
    # by a SAVEPOINT — otherwise a DB fault aborts the whole turn, including the
    # pending agent.turn_started insert.
    assert engine._db.savepoints == 1


@pytest.mark.asyncio
async def test_no_binding_and_no_covering_layer_is_no_source(monkeypatch) -> None:
    # Nothing to drop, so nothing to complain about — an agent with no knowledge
    # source must keep working under any cap.
    engine, facade_cls = _engine_with_layers([])
    _patch_facade(monkeypatch, facade_cls)

    assert await engine._has_knowledge_source(_bare_agent(), uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_no_room_means_no_concept_map_to_resolve(monkeypatch) -> None:
    engine, facade_cls = _engine_with_layers(AssertionError("must not be consulted"))
    _patch_facade(monkeypatch, facade_cls)

    assert await engine._has_knowledge_source(_bare_agent(), None) is False
    assert engine._db.savepoints == 0


@pytest.mark.asyncio
async def test_layer_lookup_failure_reports_no_source(monkeypatch) -> None:
    # Best-effort: a broken lookup must not convert a working turn into a
    # skipped one. Failing closed here means "don't complain", not "don't run".
    engine, facade_cls = _engine_with_layers(RuntimeError("db down"))
    _patch_facade(monkeypatch, facade_cls)

    assert await engine._has_knowledge_source(_bare_agent(), uuid.uuid4()) is False


def test_starvation_carries_both_terms_that_produced_the_floor() -> None:
    # The cap alone does not explain the floor: fixed_context also carries the
    # turn's input and history, so one very long message can starve a reasonable
    # cap. The skip audits both numbers because it cannot re-derive fixed_context
    # — that is computed inside the assembly closure.
    starved = te._Starvation(fixed_context=9_000, ceiling=8_000)

    assert starved.fixed_context == 9_000
    assert starved.ceiling == 8_000


# --------------------------------------------------------------------------- #
# _request_ceiling
# --------------------------------------------------------------------------- #


def _capped_agent(cap, *, mode="compact"):
    return SimpleNamespace(context_mode=SimpleNamespace(value=mode), context_token_cap=cap)


def test_request_ceiling_clamps_a_cap_above_the_providers_window() -> None:
    # context_token_cap is bounded at the DB by the *widest* provider window
    # (MAX_CONTEXT_TOKEN_CAP, gemini's 1M), not by the agent's own — so a claude
    # agent may legally carry 500k. Budgeting knowledge against that would build a
    # request no claude call could accept, which the headless path then has to
    # refuse outright: the cap must never raise the ceiling past the provider.
    assert te._request_ceiling(_capped_agent(500_000), 200_000) == 200_000


def test_request_ceiling_honours_a_cap_within_the_window() -> None:
    assert te._request_ceiling(_capped_agent(20_000), 200_000) == 20_000


def test_request_ceiling_defaults_to_the_75_percent_cap() -> None:
    agent = _capped_agent(None)
    assert te._request_ceiling(agent, 200_000) == te.ctxmod.default_cap_from_limit(200_000)


def test_request_ceiling_in_general_mode_is_the_provider_limit() -> None:
    # General mode never compacts, but R11.19 still bounds knowledge — against the
    # provider window, ignoring any configured cap.
    assert te._request_ceiling(_capped_agent(20_000, mode="general"), 200_000) == 200_000


def test_knowledge_budget_floors_at_zero_under_a_low_cap() -> None:
    # The upstream trigger AC-11 is written against: the ceiling is the agent's
    # own context_token_cap, and a low one leaves the knowledge blocks nothing.
    # 5000 - 4096 reserve - 2000 fixed is already negative, so the floor engages.
    budget = te.ctxmod.knowledge_budget(
        ceiling=5_000,
        response_reserve=te._DEFAULT_MAX_TOKENS,
        fixed_context_tokens=2_000,
        safety_margin_frac=te._KNOWLEDGE_SAFETY_MARGIN,
    )

    assert budget == 0


# --------------------------------------------------------------------------- #
# _assemble_history compaction execution — the block inside the room lock.
# No test reached it before this harness: every existing case short-circuits at
# `should_compact`.
# --------------------------------------------------------------------------- #


class _CompactSession:
    """Session double recording the ordered events the compaction block emits."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def commit(self) -> None:
        self._log.append("commit")

    async def rollback(self) -> None:  # pragma: no cover — failure paths only
        self._log.append("rollback")


def _compaction_harness(
    monkeypatch,
    *,
    summary_text: str = "SUMMARY",
    history=None,
    forced: bool = False,
):
    """Wire ``_assemble_history`` so its in-lock block actually runs.

    Returns ``(engine, agent, log, audits)``. ``log`` is the ordered event trace
    (lock enter/exit, the summary insert, commits); ``audits`` collects
    ``(action, metadata)`` pairs.
    """
    import shared_kernel.realtime.distributed_lock as dlock

    log: list[str] = []
    audits: list[tuple[str, dict]] = []

    PublisherSpy.emitted = []
    PublisherSpy.fail_on = None
    PublisherSpy.error = None
    monkeypatch.setattr(te, "Publisher", PublisherSpy)

    history = history or [
        SimpleNamespace(
            role="user", content="x", token_count=900, id=uuid.uuid4(), sender_id=uuid.uuid4(), metadata={}
        )
    ]
    monkeypatch.setattr(te.tx, "load_model_history", _async_return(history))

    class _Lock:
        def __init__(self, key, ttl_s=300) -> None:
            self._key = key

        async def __aenter__(self) -> bool:
            log.append(f"lock_enter:{self._key}")
            return True

        async def __aexit__(self, *_exc) -> bool:
            log.append("lock_exit")
            return False

    monkeypatch.setattr(dlock, "distributed_lock", _Lock)

    class _Summariser:
        def __init__(self, **_kw) -> None:
            pass

        async def summarise(self, messages, *, max_tokens: int = 2000) -> str:
            return summary_text

    monkeypatch.setattr(te, "RouterSummariser", _Summariser)

    class _Store:
        def __init__(self, _db, *, chatroom_id, agent_id=None) -> None:
            self.agent_id = agent_id

        async def replace_range_with_summary(self, *, message_ids, summary_text):
            log.append("create_message")
            return uuid.uuid4()

    monkeypatch.setattr(te.tx, "MessagesTranscriptStore", _Store)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _CompactSession(log)  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]
    engine._compact_forced_rooms = {}  # type: ignore[attr-defined]

    async def _consume(cid, _agent=None):
        # Mirrors the real implementation: a claim is tracked so a failed turn
        # can release it.
        if forced:
            engine._compact_forced_rooms[cid] = f"compact:consumed:{cid}:epoch:{_agent.id}"
        return forced

    engine._consume_compact_flag = _consume  # type: ignore[attr-defined]

    async def _audit(_agent, _room, action, extra):
        audits.append((action, extra))

    engine._audit = _audit  # type: ignore[attr-defined]

    agent = SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        context_mode=SimpleNamespace(value="compact"),
        context_token_cap=100,
    )
    return engine, agent, log, audits


async def _run_assemble(engine, agent, chatroom_id, *, room=None):
    # `room` (the liveness-beacon channel) is a keyword-only param on the real
    # `_assemble_history`, distinct from `chatroom_id` -- code-review finding:
    # this helper used to accept a single `room` param and pass it positionally
    # into `chatroom_id`, so the beacon `room` kwarg silently defaulted to None
    # for every test in this file.
    return await engine._assemble_history(
        agent, chatroom_id, 128_000, ApiKeyProvider.CLAUDE, "claude-opus-4-8", room=room
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", ["", "   "])
async def test_empty_summary_audits_compact_failed_and_keeps_history(monkeypatch, empty: str) -> None:
    # R9.11: a summarisation that produces nothing usable must leave the history
    # alone and be audited as a failure, not recorded as a successful run.
    engine, agent, log, audits = _compaction_harness(monkeypatch, summary_text=empty)
    history = await te.tx.load_model_history(None, chatroom_id=uuid.uuid4())

    out = await _run_assemble(engine, agent, uuid.uuid4())

    assert out is history
    assert "create_message" not in log
    assert [a for a, _ in audits] == ["agent.compact_failed"]


@pytest.mark.asyncio
async def test_compacting_progress_beacon_fires_with_a_live_room(monkeypatch) -> None:
    """code-review finding: no test threaded a live `room` through
    `_assemble_history`'s real (non-stubbed) compaction pass -- a future
    regression dropping `room=room` from either call site in `_run_locked`, or
    misplacing/removing the `_emit_progress(_PROGRESS_COMPACTING)` call inside
    the real in-lock branch, would ship with the whole suite green."""
    engine, agent, _log, _audits = _compaction_harness(monkeypatch)
    room = "room:test-channel"

    await _run_assemble(engine, agent, uuid.uuid4(), room=room)

    beacons = [p for c, e, p in PublisherSpy.emitted if c == room and e == "agent.progress"]
    assert {"agent_id": str(agent.id), "phase": "compacting"} in beacons


# --------------------------------------------------------------------------- #
# The one-shot /compact arming is claimed per agent (R9.09)
# --------------------------------------------------------------------------- #


class _FakeRedis:
    def __init__(self, initial=None) -> None:
        self.store: dict = dict(initial or {})

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


def _flag_engine(monkeypatch, redis):
    import shared_kernel.auth.clients as clients

    monkeypatch.setattr(clients, "get_redis", lambda: redis)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._compact_forced_rooms = {}  # type: ignore[attr-defined]
    return engine


def _mode_agent(mode: str):
    return SimpleNamespace(id=uuid.uuid4(), context_mode=SimpleNamespace(value=mode))


@pytest.mark.asyncio
async def test_each_compact_agent_claims_the_room_arming_exactly_once(monkeypatch) -> None:
    # A room-level /compact must fold once per compact-mode agent, so the
    # arming cannot be a single key the first turner deletes; and it must stay
    # one-shot per agent, so a second turn by the same agent claims nothing.
    room = uuid.uuid4()
    redis = _FakeRedis({f"compact:pending:{room}": "epoch-1"})
    engine = _flag_engine(monkeypatch, redis)
    a, b = _mode_agent("compact"), _mode_agent("compact")

    assert await engine._consume_compact_flag(room, a) is True
    assert await engine._consume_compact_flag(room, b) is True
    assert await engine._consume_compact_flag(room, a) is False


@pytest.mark.asyncio
async def test_general_mode_agent_never_claims_the_arming(monkeypatch) -> None:
    # Forcing a `general` agent to compact would fold its own history, which is
    # exactly what R9.09 forbids.
    room = uuid.uuid4()
    redis = _FakeRedis({f"compact:pending:{room}": "epoch-1"})
    engine = _flag_engine(monkeypatch, redis)

    assert await engine._consume_compact_flag(room, _mode_agent("general")) is False
    assert engine._compact_forced_rooms == {}


@pytest.mark.asyncio
async def test_no_arming_means_no_claim(monkeypatch) -> None:
    engine = _flag_engine(monkeypatch, _FakeRedis())

    assert await engine._consume_compact_flag(uuid.uuid4(), _mode_agent("compact")) is False


@pytest.mark.asyncio
async def test_releasing_a_claim_restores_only_that_agents_share(monkeypatch) -> None:
    # A failed turn must give its own agent another chance at the user's
    # one-shot /compact without re-arming agents that already folded for it.
    room = uuid.uuid4()
    redis = _FakeRedis({f"compact:pending:{room}": "epoch-1"})
    engine = _flag_engine(monkeypatch, redis)
    a, b = _mode_agent("compact"), _mode_agent("compact")
    await engine._consume_compact_flag(room, a)

    engine_b = _flag_engine(monkeypatch, redis)
    await engine_b._consume_compact_flag(room, b)

    await engine._restore_compact_flag(room)

    assert engine._compact_forced_rooms == {}
    assert await engine._consume_compact_flag(room, a) is True
    # b already folded for this epoch and must not be re-armed by a's failure.
    assert await engine_b._consume_compact_flag(room, b) is False


@pytest.mark.asyncio
async def test_releasing_without_a_claim_is_a_no_op(monkeypatch) -> None:
    room = uuid.uuid4()
    redis = _FakeRedis({f"compact:pending:{room}": "epoch-1"})
    engine = _flag_engine(monkeypatch, redis)

    await engine._restore_compact_flag(room)

    assert redis.store == {f"compact:pending:{room}": "epoch-1"}


@pytest.mark.asyncio
async def test_general_mode_agent_never_sees_a_folded_range(monkeypatch) -> None:
    # R9.09 acceptance: a `general` agent sends the entire chat history. It has
    # no cap and never compacts, so whatever a compact-mode agent folded in the
    # same room must not reach its loader as an elision. Pinned at the turn
    # engine because the defect was that `_assemble_history` passed no reader
    # identity to the loader at all.
    from contexts.conversation.interfaces.facade import Message, SenderType

    room, compactor = uuid.uuid4(), uuid.uuid4()

    def _m(content, **meta):
        return Message(
            id=uuid.uuid4(),
            chatroom_id=room,
            sender_type=SenderType.USER,
            sender_id=None,
            content_md=content,
            metadata=meta,
        )

    m1, m2 = _m("one"), _m("two")
    summary = Message(
        id=uuid.uuid4(),
        chatroom_id=room,
        sender_type=SenderType.SYSTEM,
        sender_id=None,
        content_md="SUMMARY",
        metadata={
            "type": "compact_summary",
            "compacted_ids": [str(m1.id), str(m2.id)],
            "producer_agent_id": str(compactor),
        },
    )

    class _Facade:
        def __init__(self, _db) -> None:
            pass

        async def list_messages(self, chatroom_id, *, limit=100, before_id=None):
            return [summary, m2, m1]  # newest-first

        async def list_attachments_for_messages(self, message_ids):
            return {}

    monkeypatch.setattr(te.tx, "ConversationFacade", _Facade)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]

    general = SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        context_mode=SimpleNamespace(value="general"),
        context_token_cap=None,
    )
    out = await _run_assemble(engine, general, room)

    assert [h.id for h in out] == [m1.id, m2.id]
    assert all(h.content != "SUMMARY" for h in out)


@pytest.mark.asyncio
async def test_compaction_commits_before_releasing_the_room_lock(monkeypatch) -> None:
    # The room lock guards against two agents folding overlapping ranges, but a
    # staged row is invisible to another session under READ COMMITTED. The
    # exclusion only holds if the commit happens inside the lock.
    engine, agent, log, audits = _compaction_harness(monkeypatch)

    await _run_assemble(engine, agent, uuid.uuid4())

    trimmed = [e.split(":")[0] for e in log]
    assert trimmed == ["lock_enter", "create_message", "commit", "lock_exit"]
    assert [a for a, _ in audits] == ["agent.compact_run"]


@pytest.mark.asyncio
async def test_the_compaction_lock_is_scoped_to_the_producing_agent(monkeypatch) -> None:
    # Once a fold is scoped to its producer, two agents folding concurrently
    # write independent rows and cannot conflict — a room-wide key would make
    # the second agent abandon legitimate work instead of serialising. What
    # still needs excluding is one agent folding twice at once, which the turn
    # lock misses because `run_compaction` runs headless without it.
    engine, agent, log, _audits = _compaction_harness(monkeypatch)
    room = uuid.uuid4()

    await _run_assemble(engine, agent, room)

    enter = next(e for e in log if e.startswith("lock_enter"))
    assert enter == f"lock_enter:compact:lock:{room}:{agent.id}"


@pytest.mark.asyncio
async def test_forced_compact_flag_is_not_restored_after_a_committed_fold(monkeypatch) -> None:
    # Pins the hazard the commit-inside-the-lock fix introduces: the fold is now
    # durable, so a later rollback cannot undo it. Re-arming the one-shot flag
    # would force a second fold of a request already served.
    engine, agent, log, _audits = _compaction_harness(monkeypatch, forced=True)
    room = uuid.uuid4()

    await _run_assemble(engine, agent, room)

    assert "create_message" in log
    assert room not in engine._compact_forced_rooms
    # `_restore_compact_flag` no-ops on a room absent from that set, so the
    # turn's own failure path can no longer re-arm this one.
    await engine._restore_compact_flag(room)


@pytest.mark.asyncio
async def test_a_failed_compaction_keeps_the_forced_compact_claim(monkeypatch) -> None:
    # The claim is deliberately consumed on failure. Releasing it would re-arm
    # this agent against an epoch that lives for an hour, and the forced path
    # skips the cap check — so a persistently blank summariser would issue a
    # fresh billed call on every turn of that agent for the rest of the hour.
    # The request is not lost silently: agent.compact_failed records it (R9.11).
    engine, agent, log, audits = _compaction_harness(monkeypatch, summary_text="", forced=True)
    room = uuid.uuid4()
    restored: list = []

    async def _restore(cid) -> None:
        restored.append(cid)

    engine._restore_compact_flag = _restore  # type: ignore[attr-defined]

    await _run_assemble(engine, agent, room)

    assert "create_message" not in log
    assert [a for a, _ in audits] == ["agent.compact_failed"]
    assert restored == []


@pytest.mark.asyncio
async def test_a_fold_that_does_nothing_still_re_arms_the_forced_flag(monkeypatch) -> None:
    # Complement to the test above: when `run_compact` declines (nothing left to
    # fold), no row was committed, so the user's one-shot /compact must survive.
    engine, agent, log, _audits = _compaction_harness(
        monkeypatch,
        forced=True,
        history=[
            SimpleNamespace(
                role="system",
                content="S",
                token_count=900,
                id=uuid.uuid4(),
                sender_id=None,
                metadata={"type": "compact_summary", "compacted_ids": []},
            )
        ],
    )
    room = uuid.uuid4()
    restored: list = []

    async def _restore(cid) -> None:
        restored.append(cid)

    engine._restore_compact_flag = _restore  # type: ignore[attr-defined]

    await _run_assemble(engine, agent, room)

    assert "create_message" not in log
    assert "commit" not in log
    assert restored == [room]
