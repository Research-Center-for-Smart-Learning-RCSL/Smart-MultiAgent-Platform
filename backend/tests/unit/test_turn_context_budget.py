"""F-16 / F-17 — whole-request token budgeting for turn assembly.

Covers the pieces the room path wires together:
- ``_assemble_agent_knowledge`` distributes per-source budgets and omits a
  zero-budget source (F-16 allocator wiring).
- ``_assemble_history`` decides compaction against the *assembled* request
  (history + non-knowledge prefix), not history alone (F-17).

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
