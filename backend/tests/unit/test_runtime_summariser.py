"""Unit tests for `contexts.agents.application.runtime.summariser` (K.2, R9.10).

The module's contract is that *any* failure propagates so `context.run_compact`
can wrap it into `CompactFailed` and the turn keeps its un-compacted history
(R9.11). A 200 carrying blank text is such a failure; it used to be returned as
a valid summary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from contexts.agents.application.runtime.summariser import RouterSummariser
from contexts.keys.domain.providers import ApiKeyProvider


@dataclass
class _Msg:
    id: int = 1
    role: str = "user"
    content: str = "hello"
    metadata: dict[str, Any] | None = None
    token_count: int = 10


@dataclass
class _Result:
    http_status: int
    body: dict[str, Any]


class _FakeRouter:
    def __init__(self, result: _Result) -> None:
        self._result = result
        self.calls = 0

    async def call(self, *, group_id, request):
        self.calls += 1
        return self._result


def _summariser(result: _Result) -> tuple[RouterSummariser, _FakeRouter]:
    router = _FakeRouter(result)
    return (
        RouterSummariser(
            router=router,  # type: ignore[arg-type]
            key_group_id=uuid.uuid4(),
            provider=ApiKeyProvider.CLAUDE,
            model="claude-sonnet-5",
            agent_id=uuid.uuid4(),
        ),
        router,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "\n\t", "　"])
async def test_summarise_raises_on_empty_text_at_status_200(text: str) -> None:
    summariser, router = _summariser(_Result(200, {"text": text}))
    with pytest.raises(RuntimeError, match="empty text"):
        await summariser.summarise([_Msg()])  # type: ignore[list-item]
    assert router.calls == 1


@pytest.mark.asyncio
async def test_summarise_raises_on_missing_text_key() -> None:
    # Guards the `.get("text", "")` default: a body shaped differently by a new
    # adapter must not silently become an empty summary.
    summariser, _ = _summariser(_Result(200, {}))
    with pytest.raises(RuntimeError, match="empty text"):
        await summariser.summarise([_Msg()])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_summarise_raises_on_non_200() -> None:
    summariser, _ = _summariser(_Result(503, {"text": "ignored"}))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        await summariser.summarise([_Msg()])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_summarise_passes_through_valid_text() -> None:
    # Regression guard against over-rejecting: a genuinely short summary of a
    # short range is legitimate and must not be treated as a failure.
    summariser, _ = _summariser(_Result(200, {"text": "ok"}))
    assert await summariser.summarise([_Msg()]) == "ok"  # type: ignore[list-item]
