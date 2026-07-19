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

    async def _no_flag(_cid):
        return False

    engine._consume_compact_flag = _no_flag  # type: ignore[attr-defined]

    seen: dict = {}

    def _spy_should(*, mode, projected_tokens, context_token_cap, provider_context_limit):
        seen["projected"] = projected_tokens
        return False  # short-circuit before the lock/compaction machinery

    monkeypatch.setattr(te.ctxmod, "should_compact", _spy_should)

    agent = SimpleNamespace(
        context_mode=SimpleNamespace(value="compact"),
        context_token_cap=1000,
    )
    out = await engine._assemble_history(agent, uuid.uuid4(), 128_000, {}, extra_projected_tokens=800)

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
