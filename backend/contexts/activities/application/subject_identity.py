"""Which kind of person a subject id names ([R30.39], 0098).

``activity_sessions.subject_user_id`` holds a ``users.id`` or an anonymous
guest's ``guest_sessions.id``, and the stored ``subject_kind`` says which. The
kind is resolved from the id itself rather than from who is calling: an admin
acting for a guest subject is not a guest, and a caller-derived kind would record
that guest as a user.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.interfaces.facade import ConversationFacade


async def is_guest_subject(db: AsyncSession, subject_user_id: uuid.UUID) -> bool:
    return await ConversationFacade(db).is_guest_session(subject_user_id)
