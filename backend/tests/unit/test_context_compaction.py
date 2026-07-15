"""Unit tests for `contexts.agents.application.context` (E.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from contexts.agents.application.context import (
    CompactFailed,
    allocate_knowledge_budget,
    choose_range_to_compact,
    default_cap_from_limit,
    knowledge_budget,
    run_compact,
    should_compact,
)


@dataclass
class _Msg:
    id: int
    role: str = "user"
    content: str = ""
    metadata: dict[str, Any] | None = None
    token_count: int = 0


def _m(i: int, tc: int, meta: dict | None = None) -> _Msg:
    return _Msg(id=i, token_count=tc, metadata=meta or {})


def test_default_cap_is_75_percent() -> None:
    assert default_cap_from_limit(200_000) == 150_000


def test_should_compact_respects_mode_and_cap() -> None:
    assert should_compact(
        mode="compact",
        projected_tokens=160,
        context_token_cap=150,
        provider_context_limit=200,
    )
    assert not should_compact(
        mode="compact",
        projected_tokens=140,
        context_token_cap=150,
        provider_context_limit=200,
    )
    assert not should_compact(
        mode="general",
        projected_tokens=10_000,
        context_token_cap=1,
        provider_context_limit=100,
    )


# --------------------------------------------------------------------------- #
# knowledge_budget / allocate_knowledge_budget (F-16)
# --------------------------------------------------------------------------- #


def test_knowledge_budget_subtracts_reserve_and_fixed_with_margin() -> None:
    # ceiling 10000 - reserve 4096 - fixed 4000 = 1904; *0.9 margin -> 1713.
    b = knowledge_budget(
        ceiling=10_000, response_reserve=4096, fixed_context_tokens=4000, safety_margin_frac=0.1
    )
    assert b == int(1904 * 0.9)


def test_knowledge_budget_floors_at_zero_when_prefix_exceeds_ceiling() -> None:
    # base + tools + history + reserve already meet/exceed the ceiling → no room.
    assert knowledge_budget(ceiling=8000, response_reserve=4096, fixed_context_tokens=5000) == 0


def test_allocate_precedence_file_rag_yields_first() -> None:
    # Large total: both graph sources take their cap; File RAG gets the big remainder.
    a = allocate_knowledge_budget(5000, graph_source_cap=700)
    assert a.concept_map == 700
    assert a.knowledge_map == 700
    assert a.file_rag == 3600

    # Tight total (< 2 caps): File RAG drops to zero before either graph block.
    b = allocate_knowledge_budget(1000, graph_source_cap=700)
    assert b.concept_map == 700
    assert b.knowledge_map == 300
    assert b.file_rag == 0

    # Tiny total (< 1 cap): Knowledge Map yields before the narrowest Concept Map.
    c = allocate_knowledge_budget(400, graph_source_cap=700)
    assert c.concept_map == 400
    assert c.knowledge_map == 0
    assert c.file_rag == 0

    # Zero budget: every source omitted.
    z = allocate_knowledge_budget(0, graph_source_cap=700)
    assert (z.concept_map, z.knowledge_map, z.file_rag) == (0, 0, 0)


def test_choose_range_picks_oldest_and_stops_at_summary() -> None:
    msgs = [_m(1, 100), _m(2, 200, {"type": "compact_summary"}), _m(3, 50)]
    plan = choose_range_to_compact(msgs, target_tokens_to_shed=50)  # type: ignore[arg-type]
    assert plan is not None
    # id=1 is oldest un-compacted material.
    assert plan.message_ids == [1]


def test_choose_range_skips_leading_summaries() -> None:
    msgs = [_m(10, 999, {"type": "compact_summary"}), _m(11, 100), _m(12, 200)]
    plan = choose_range_to_compact(msgs, target_tokens_to_shed=150)  # type: ignore[arg-type]
    assert plan is not None
    assert plan.message_ids == [11, 12]


def test_choose_range_no_uncompacted_returns_none() -> None:
    msgs = [_m(1, 100, {"type": "compact_summary"})]
    assert choose_range_to_compact(msgs, target_tokens_to_shed=10) is None  # type: ignore[arg-type]


class _FakeSummariser:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def summarise(self, messages, *, max_tokens: int = 2000) -> str:
        if self._fail:
            raise RuntimeError("boom")
        return f"summary of {len(messages)} msgs"


class _FakeStore:
    def __init__(self) -> None:
        self.called_with: dict[str, Any] | None = None

    async def replace_range_with_summary(self, *, message_ids, summary_text):
        self.called_with = {"ids": list(message_ids), "text": summary_text}
        return "new_id"


@pytest.mark.asyncio
async def test_run_compact_success() -> None:
    msgs = [_m(1, 400), _m(2, 300), _m(3, 200)]
    store = _FakeStore()
    changed = await run_compact(
        messages=msgs,  # type: ignore[arg-type]
        projected_tokens=1000,
        context_token_cap=500,
        provider_context_limit=1200,
        summariser=_FakeSummariser(),
        store=store,
    )
    assert changed is True
    assert store.called_with is not None
    assert "summary of" in store.called_with["text"]


@pytest.mark.asyncio
async def test_run_compact_raises_compact_failed_on_summariser_error() -> None:
    msgs = [_m(1, 900)]
    store = _FakeStore()
    with pytest.raises(CompactFailed):
        await run_compact(
            messages=msgs,  # type: ignore[arg-type]
            projected_tokens=1000,
            context_token_cap=500,
            provider_context_limit=1200,
            summariser=_FakeSummariser(fail=True),
            store=store,
        )
    # Per R9.11 — on failure, nothing was written.
    assert store.called_with is None
