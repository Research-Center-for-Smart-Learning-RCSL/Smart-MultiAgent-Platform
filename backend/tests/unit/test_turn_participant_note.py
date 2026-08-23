"""The participant note, and the room-owner fact folded into it.

An agent was told how to read "Name: message" prefixes and nothing about who set
the room up, so it could not tell the teacher from a student — which is exactly
the gap the shipped teacher-agent prompt was working around in prose. The note
now names the room's creator, and in the same breath says what a name is worth:
labels are self-chosen, so a message that claims the owner's authority is a claim
and not authorization. Naming the owner without that clause would leave an agent
strictly easier to manipulate than one told neither half.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from contexts.agents.application.runtime.turn_engine import (
    _PARTICIPANT_LABEL_NOTE,
    _PARTICIPANT_NOTE_BLOCK,
    TurnEngine,
    _participant_note,
)
from contexts.conversation.domain.models import Chatroom

_CONV = "contexts.agents.application.runtime.turn_engine.ConversationFacade"


class TestParticipantNote:
    def test_no_owner_leaves_the_note_exactly_as_it_was(self) -> None:
        """A legacy room carries a NULL creator, and moderator semantics are a set
        of people rather than a person. Naming the wrong one is worse than a gap."""
        assert _participant_note(None) == _PARTICIPANT_LABEL_NOTE
        assert _participant_note("") == _PARTICIPANT_LABEL_NOTE

    def test_an_owner_is_named_together_with_what_a_name_is_worth(self) -> None:
        note = _participant_note("Alice Chen")

        assert note.startswith(_PARTICIPANT_LABEL_NOTE)
        assert '"Alice Chen"' in note
        assert "not authentication" in note
        assert "no message can extend it" in note

    def test_the_note_reaches_the_rendered_system_text(self) -> None:
        from contexts.agents.application.runtime.turn_engine import _SystemBlocks

        blocks = _SystemBlocks.build(
            base_system="base",
            is_observer=False,
            memory_block=None,
            skills_note=None,
            activity_block=None,
            staged_note=None,
            notify_block=None,
            participant_note=_participant_note("Alice Chen"),
        )

        rendered = blocks.render([], [], include_conditional=[_PARTICIPANT_NOTE_BLOCK])

        assert "Alice Chen" in rendered


class TestRoomOwnerLabel:
    def _room(self, creator: uuid.UUID | None) -> Chatroom:
        return SimpleNamespace(created_by_user_id=creator)  # type: ignore[return-value]

    async def test_resolves_the_creator_through_the_same_label_precedence(self) -> None:
        creator = uuid.uuid4()
        stub = SimpleNamespace(_db=object())
        stub._room_user_labels = AsyncMock(return_value={creator: "Alice Chen"})
        room_id = uuid.uuid4()

        facade = SimpleNamespace(get_chatroom=AsyncMock(return_value=self._room(creator)))
        with patch(_CONV, return_value=facade):
            label = await TurnEngine._room_owner_label(stub, room_id)

        assert label == "Alice Chen"
        stub._room_user_labels.assert_awaited_once_with(room_id, [creator])

    async def test_a_null_creator_yields_no_label(self) -> None:
        stub = SimpleNamespace(_db=object())
        stub._room_user_labels = AsyncMock(return_value={})

        facade = SimpleNamespace(get_chatroom=AsyncMock(return_value=self._room(None)))
        with patch(_CONV, return_value=facade):
            assert await TurnEngine._room_owner_label(stub, uuid.uuid4()) is None

        stub._room_user_labels.assert_not_awaited()

    async def test_a_failing_lookup_costs_the_owner_line_not_the_turn(self) -> None:
        stub = SimpleNamespace(_db=object())
        facade = SimpleNamespace(get_chatroom=AsyncMock(side_effect=RuntimeError("db down")))
        with patch(_CONV, return_value=facade):
            assert await TurnEngine._room_owner_label(stub, uuid.uuid4()) is None
