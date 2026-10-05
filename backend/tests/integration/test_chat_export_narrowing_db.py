"""Narrowed chat export predicate against a real database (audit F-11).

Dossier ``docs/tasks/2026-10-05-chat-export-guest-narrowing``: matrix row 19 keeps
the caller's own messages plus agent and system messages. Exercises
``MessageRepository.all_for_chatroom``, the query the export service calls.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.infrastructure.repositories import MessageRepository

pytestmark = pytest.mark.db


async def test_narrowed_export_keeps_only_own_agent_and_system_messages(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, caller = project
    workspace_id, room_id = uuid.uuid4(), uuid.uuid4()
    senders = {
        "own": ("user", caller),
        "other_member": ("user", uuid.uuid4()),
        "guest": ("guest", uuid.uuid4()),
        "agent": ("agent", uuid.uuid4()),
        "system": ("system", None),
    }
    ids = {key: uuid.uuid4() for key in senders}

    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="f11")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=room_id, workspace_id=workspace_id, name="f11", guest_token=str(uuid.uuid4())
            )
        )
        for key, (sender_type, sender_id) in senders.items():
            await session.execute(
                ct.messages.insert().values(
                    id=ids[key],
                    chatroom_id=room_id,
                    sender_type=sender_type,
                    sender_id=sender_id,
                    content_md=key,
                )
            )
        await session.commit()

        repo = MessageRepository(session)
        narrowed = {m.id for m in await repo.all_for_chatroom(room_id, own_user_id=caller)}
        full = {m.id for m in await repo.all_for_chatroom(room_id)}

    assert narrowed == {ids["own"], ids["agent"], ids["system"]}
    assert full == set(ids.values())
