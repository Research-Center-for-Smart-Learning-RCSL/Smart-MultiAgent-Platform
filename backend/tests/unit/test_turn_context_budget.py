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
from contexts.agents.application.context import KnowledgeBudgets


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


@pytest.mark.asyncio
async def test_assemble_knowledge_passes_per_source_budgets() -> None:
    engine = te.TurnEngine.__new__(te.TurnEngine)
    seen: dict = {}

    async def _rag(agent, queries, *, token_budget=None):
        seen["rag"] = token_budget
        return _RagCtx("RAG")

    async def _graph(agent, chatroom_id, queries, *, token_budget=None):
        seen["concept"] = token_budget
        return "CONCEPT"

    async def _km(agent, queries, *, token_budget=None):
        seen["knowmap"] = token_budget
        return "KNOWMAP"

    engine._rag_context = _rag  # type: ignore[attr-defined]
    engine._graphrag_context = _graph  # type: ignore[attr-defined]
    engine._knowmap_context = _km  # type: ignore[attr-defined]

    budgets = KnowledgeBudgets(concept_map=700, knowledge_map=700, file_rag=3600)
    blocks, _rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budgets=budgets
    )

    assert blocks == ["RAG", "CONCEPT", "KNOWMAP"]
    # Each provider is capped to its own precedence-allocated budget.
    assert seen == {"rag": 3600, "concept": 700, "knowmap": 700}


@pytest.mark.asyncio
async def test_assemble_knowledge_omits_zero_budget_source() -> None:
    engine = te.TurnEngine.__new__(te.TurnEngine)
    called: dict = {}

    async def _rag(agent, queries, *, token_budget=None):
        called["rag"] = True
        return _RagCtx("RAG")

    async def _graph(agent, chatroom_id, queries, *, token_budget=None):
        called["concept"] = True
        return "CONCEPT"

    async def _km(agent, queries, *, token_budget=None):
        called["knowmap"] = True
        return "KNOWMAP"

    engine._rag_context = _rag  # type: ignore[attr-defined]
    engine._graphrag_context = _graph  # type: ignore[attr-defined]
    engine._knowmap_context = _km  # type: ignore[attr-defined]

    # File RAG granted zero: it is omitted entirely (never queried, never empty).
    budgets = KnowledgeBudgets(concept_map=700, knowledge_map=300, file_rag=0)
    blocks, _rag_ctx = await engine._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=uuid.uuid4(), budgets=budgets
    )

    assert "rag" not in called  # zero-budget File RAG never queried
    assert blocks == ["CONCEPT", "KNOWMAP"]


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
