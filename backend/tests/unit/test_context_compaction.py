"""Unit tests for `contexts.agents.application.context` (E.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from contexts.agents.application.context import (
    CompactFailed,
    choose_range_to_compact,
    default_cap_from_limit,
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


@pytest.mark.asyncio()
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


@pytest.mark.asyncio()
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
