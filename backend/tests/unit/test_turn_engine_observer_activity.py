"""Turn-engine activity delegation (AC-1, AC-2).

Building a full TurnEngine needs settings/router/qdrant wiring, so these exercise
the delegating ``_activity_context`` as an unbound method over a stub — proving it
forwards to the provider and returns ``None`` when the room has no activities (the
coverage gate every turn relies on). Agent-visibility follow-up: the call site in
``turn_engine.py`` (``run_turn``) no longer gates this behind ``is_observer`` —
every agent's turn gets the block now, observer or not; per-row content gating
lives entirely in the provider (``ActivityContextProvider``/
``RecentActivityRow.expose_payload_to_agent``, covered in
``test_activity_context_provider.py``). The unconditional call-site placement is
exercised end-to-end by the integration suite, not here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.agents.application.runtime.turn_engine import TurnEngine


class TestActivityContextDelegation:
    async def test_delegates_to_provider_and_returns_block(self) -> None:
        stub = SimpleNamespace(_activity_provider=SimpleNamespace())
        stub._activity_provider.query = AsyncMock(return_value="[Recent room activity]\n- x")
        room = uuid.uuid4()

        result = await TurnEngine._activity_context(stub, room)

        assert result == "[Recent room activity]\n- x"
        stub._activity_provider.query.assert_awaited_once_with(chatroom_id=room)

    async def test_returns_none_when_provider_gates_off(self) -> None:
        stub = SimpleNamespace(_activity_provider=SimpleNamespace())
        stub._activity_provider.query = AsyncMock(return_value=None)

        result = await TurnEngine._activity_context(stub, uuid.uuid4())

        assert result is None
