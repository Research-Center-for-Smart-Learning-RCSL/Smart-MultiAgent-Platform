"""What the draft grant and disclosure writes record on the audit trail (§32).

[R32.06] says every grant change and every disclosure change is audited, and that
draft content and participant identifiers never appear in audit metadata. The second
half is the one worth a test: the first is visible in review, and the second is an
absence, which is exactly the kind of property that survives a well-meaning "let's
include the drafts we cleared" edit unless something asserts it.

The repository half runs against a real PostgreSQL in
``tests/integration/test_draft_grant_repository.py`` — the shared grantor column and
the fail-closed null-grantor arm are row facts, not statement facts. This file covers
what the *service* layer adds on top: which events fire, and what they carry.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.conversation.application.chatroom_service import ChatroomFlagsPatch, ChatroomService

ROOM = uuid.uuid4()
AGENT = uuid.uuid4()
ACTOR = uuid.uuid4()


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every `audit.emit` call the service makes, in order."""
    recorded: list[Any] = []

    async def _emit(_db: object, event: Any, **_kw: Any) -> bool:
        recorded.append(event)
        return True

    monkeypatch.setattr("contexts.conversation.application.chatroom_service.audit.emit", _emit)
    return recorded


def _service(**agent_repo: Any) -> ChatroomService:
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.info = {}
    service = ChatroomService(db)
    for name, value in agent_repo.items():
        setattr(service._agents, name, value)
    return service


class TestTheGrantWrite:
    async def test_a_written_grant_is_audited_by_agent_and_state_only(self, emitted: list[Any]) -> None:
        service = _service(set_draft_grant=AsyncMock(return_value=True))

        assert await service.set_agent_draft_grant(
            chatroom_id=ROOM, agent_id=AGENT, granted=True, actor_user_id=ACTOR, actor_ip="10.0.0.1"
        )

        assert len(emitted) == 1
        event = emitted[0]
        assert event.action == "chatroom.agent_draft_grant_updated"
        assert event.actor_user_id == ACTOR
        assert event.resource_id == ROOM
        assert set(event.metadata) == {"agent_id", "granted"}
        assert event.metadata == {"agent_id": str(AGENT), "granted": True}

    async def test_a_revoke_is_audited_too(self, emitted: list[Any]) -> None:
        """An operator answering "when did this stop" needs the closing edge as much
        as the opening one."""
        service = _service(set_draft_grant=AsyncMock(return_value=True))

        await service.set_agent_draft_grant(
            chatroom_id=ROOM, agent_id=AGENT, granted=False, actor_user_id=ACTOR, actor_ip=None
        )

        assert emitted[0].metadata["granted"] is False

    async def test_an_unbound_agent_writes_nothing_and_audits_nothing(self, emitted: list[Any]) -> None:
        """The route turns ``False`` into a 404. An audit row here would record a
        grant change that did not happen."""
        service = _service(set_draft_grant=AsyncMock(return_value=False))

        assert (
            await service.set_agent_draft_grant(
                chatroom_id=ROOM, agent_id=AGENT, granted=True, actor_user_id=ACTOR, actor_ip=None
            )
            is False
        )

        assert emitted == []

    async def test_no_draft_content_can_reach_the_trail(self, emitted: list[Any]) -> None:
        """[R32.06]'s absence, asserted as an absence.

        The metadata is a closed two-key dict by construction; this pins that, so a
        later edit adding "what was cleared" or "how many drafts existed" fails here
        rather than shipping unsent text into the audit log.
        """
        service = _service(set_draft_grant=AsyncMock(return_value=True))

        await service.set_agent_draft_grant(
            chatroom_id=ROOM, agent_id=AGENT, granted=True, actor_user_id=ACTOR, actor_ip=None
        )

        rendered = repr(emitted[0].metadata)
        for forbidden in ("content", "draft", "surface", "user_id", "composer"):
            assert forbidden not in rendered, f"audit metadata mentions {forbidden!r}: {rendered}"


class TestTheDisclosurePatch:
    def _room(self, **flags: Any) -> SimpleNamespace:
        base = {
            "allow_member_groups": False,
            "allow_project_members": True,
            "disclose_observers": True,
            "disclose_drafts": True,
        }
        return SimpleNamespace(**{**base, **flags})

    async def _patch(
        self, emitted: list[Any], current: SimpleNamespace, patch: ChatroomFlagsPatch
    ) -> list[str]:
        service = _service()
        service.get = AsyncMock(return_value=current)  # type: ignore[method-assign]
        service._rooms.update = AsyncMock(return_value=current)  # type: ignore[method-assign]

        await service.patch(
            chatroom_id=ROOM,
            expected_version=1,
            patch=patch,
            actor_user_id=ACTOR,
            actor_ip=None,
        )
        return [e.action for e in emitted]

    async def test_turning_draft_disclosure_off_emits_its_own_event(self, emitted: list[Any]) -> None:
        """A distinct action rather than one shared event carrying a field name: an
        operator asking "when did this room stop telling people their unsent text is
        readable" must not have to filter another action's metadata to find it."""
        actions = await self._patch(
            emitted, self._room(disclose_drafts=True), ChatroomFlagsPatch(disclose_drafts=False)
        )

        assert actions == ["chatroom.updated", "chatroom.draft_disclosure_changed"]
        assert emitted[1].metadata == {"old": True, "new": False}

    async def test_a_no_op_patch_emits_no_disclosure_event(self, emitted: list[Any]) -> None:
        """Setting a flag to the value it already holds changed nothing, and a trail
        that records it makes every real change harder to find."""
        actions = await self._patch(
            emitted, self._room(disclose_drafts=True), ChatroomFlagsPatch(disclose_drafts=True)
        )

        assert actions == ["chatroom.updated"]

    async def test_the_two_disclosure_flags_are_audited_independently(self, emitted: list[Any]) -> None:
        """They are separate consent decisions about separate surfaces, and a patch
        may legitimately move both."""
        actions = await self._patch(
            emitted,
            self._room(disclose_observers=True, disclose_drafts=True),
            ChatroomFlagsPatch(disclose_observers=False, disclose_drafts=False),
        )

        assert actions == [
            "chatroom.updated",
            "chatroom.disclosure_changed",
            "chatroom.draft_disclosure_changed",
        ]

    async def test_observer_disclosure_alone_does_not_emit_the_draft_event(self, emitted: list[Any]) -> None:
        """The regression the paired loop is most likely to introduce: reading one
        flag's `is not None` and emitting the other's event."""
        actions = await self._patch(emitted, self._room(), ChatroomFlagsPatch(disclose_observers=False))

        assert "chatroom.draft_disclosure_changed" not in actions
