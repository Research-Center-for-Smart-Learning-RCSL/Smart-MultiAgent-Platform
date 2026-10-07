"""Agent ids a room's history names, against a real database (name-fallback AC-2).

Dossier ``docs/tasks/2026-10-07-name-fallback-surfaces``: the agent-label read names
the agents that authored a live message and the observer a released observation
disclosed, and nothing else. Exercises ``MessageRepository.agent_label_ids``.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.infrastructure.repositories import MessageRepository

pytestmark = pytest.mark.db


async def test_authors_and_disclosed_observers_only(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, _ = project
    workspace_id, room_id, other_room = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    author, deleted_message_author, disclosed, foreign = (uuid.uuid4() for _ in range(4))

    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="labels")
        )
        for rid in (room_id, other_room):
            await session.execute(
                ct.chatrooms.insert().values(
                    id=rid, workspace_id=workspace_id, name="labels", guest_token=str(uuid.uuid4())
                )
            )
        rows: list[dict[str, object]] = [
            {"chatroom_id": room_id, "sender_type": "agent", "sender_id": author},
            {"chatroom_id": room_id, "sender_type": "agent", "sender_id": author},
            {"chatroom_id": room_id, "sender_type": "user", "sender_id": uuid.uuid4()},
            {
                "chatroom_id": room_id,
                "sender_type": "system",
                "sender_id": None,
                "metadata": {"type": "released_observation", "observer_agent_id": str(disclosed)},
            },
            # Released with disclosure off: no observer id travels, so none is named.
            {
                "chatroom_id": room_id,
                "sender_type": "system",
                "sender_id": None,
                "metadata": {"type": "released_observation"},
            },
            # Only released observations disclose; the key elsewhere is not a disclosure.
            {
                "chatroom_id": room_id,
                "sender_type": "system",
                "sender_id": None,
                "metadata": {"type": "summary", "observer_agent_id": str(uuid.uuid4())},
            },
            {"chatroom_id": other_room, "sender_type": "agent", "sender_id": foreign},
        ]
        for row in rows:
            await session.execute(ct.messages.insert().values(id=uuid.uuid4(), content_md="x", **row))
        await session.execute(
            ct.messages.insert().values(
                id=uuid.uuid4(),
                chatroom_id=room_id,
                sender_type="agent",
                sender_id=deleted_message_author,
                content_md="x",
                deleted_at=sa.func.now(),
            )
        )
        await session.commit()

        ids = await MessageRepository(session).agent_label_ids(room_id)
        # The cap applies to the union in a fixed order, so a capped read is stable.
        capped = await MessageRepository(session).agent_label_ids(room_id, limit=1)

    assert ids == {author, disclosed}
    assert capped == {min(author, disclosed, key=str)}
