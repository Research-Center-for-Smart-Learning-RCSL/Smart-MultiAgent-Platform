"""``ConversationFacade.latest_user_attachments`` counts a guest as a human -- AC-7.

Spec: ``docs/tasks/2026-10-05-guest-identity-foreign-keys/spec.md`` §7.6.

The fallback resolver serves turns with no trigger message. The transcript maps a
guest sender to role ``user`` and the engine splices fallback attachments onto
the newest ``user``-role row, so a resolver that skipped guests staged an older
member's file as if it belonged to the guest's message.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.conversation.domain.models import AttachmentStatus, SenderType
from contexts.conversation.interfaces.facade import ConversationFacade


def _facade(
    messages: list[SimpleNamespace], attachments: dict[uuid.UUID, list[SimpleNamespace]]
) -> ConversationFacade:
    facade = object.__new__(ConversationFacade)
    facade._messages = SimpleNamespace(list=AsyncMock(return_value=messages))  # type: ignore[attr-defined]

    async def _for(message_id: uuid.UUID) -> list[SimpleNamespace]:
        return attachments.get(message_id, [])

    facade._attachments = SimpleNamespace(list_for_message=_for)  # type: ignore[attr-defined]
    return facade


def _msg(sender_type: SenderType) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sender_type=sender_type)


def _file(name: str) -> SimpleNamespace:
    return SimpleNamespace(filename=name, status=AttachmentStatus.ACTIVE)


async def test_the_newest_human_message_wins_when_it_is_a_guests() -> None:
    guest_msg, member_msg = _msg(SenderType.GUEST), _msg(SenderType.USER)
    facade = _facade(
        # Newest first, as MessageRepository.list returns them.
        [guest_msg, member_msg],
        {guest_msg.id: [_file("guest.pdf")], member_msg.id: [_file("member.pdf")]},
    )
    result = await facade.latest_user_attachments(uuid.uuid4())
    assert [a.filename for a in result] == ["guest.pdf"]


async def test_an_attachment_less_guest_message_does_not_fall_through_to_a_member_file() -> None:
    """Matches what an attachment-less member message already does: the newest
    human message decides, and it has nothing to stage."""
    guest_msg, member_msg = _msg(SenderType.GUEST), _msg(SenderType.USER)
    facade = _facade([guest_msg, member_msg], {member_msg.id: [_file("member.pdf")]})
    assert await facade.latest_user_attachments(uuid.uuid4()) == []


async def test_agent_and_system_messages_are_still_skipped() -> None:
    agent_msg, system_msg, member_msg = _msg(SenderType.AGENT), _msg(SenderType.SYSTEM), _msg(SenderType.USER)
    facade = _facade([agent_msg, system_msg, member_msg], {member_msg.id: [_file("member.pdf")]})
    result = await facade.latest_user_attachments(uuid.uuid4())
    assert [a.filename for a in result] == ["member.pdf"]
