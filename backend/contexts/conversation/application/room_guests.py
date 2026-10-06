"""Which of a room's participants are guests, and what they call themselves ([R13.33]).

One read shared by the member roster and the agent turn's labels, so the
participant list a classmate reads and the transcript an agent reads mark the
same people.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomRepository,
    GuestSessionRepository,
    WorkspaceRepository,
)
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver


@dataclass(frozen=True, slots=True)
class RoomGuests:
    # Anonymous guest session id (its messages' sender_id) -> chosen name.
    sessions: dict[uuid.UUID, str]
    # Every `chatroom_guests` row: user id -> room label. Includes members who
    # enrolled through the link before enrolment skipped them; their room label
    # still wins the label precedence, but they are not guest identities.
    registered: dict[uuid.UUID, str | None]
    # The registered guests who are guest identities: no role in the room's
    # project and not a platform admin.
    unaffiliated: frozenset[uuid.UUID]

    @property
    def labels(self) -> dict[uuid.UUID, str | None]:
        # Session ids and user ids are independent random UUIDs, so the merge
        # cannot collide.
        return {**self.sessions, **self.registered}

    @property
    def identity_ids(self) -> frozenset[uuid.UUID]:
        """Every id whose label carries the guest marker."""
        return frozenset(self.sessions) | self.unaffiliated


async def load_room_guests(db: AsyncSession, chatroom_id: uuid.UUID) -> RoomGuests:
    sessions = dict(await GuestSessionRepository(db).list_labels(chatroom_id))
    registered = {g.user_id: g.display_name for g in await ChatroomGuestRepository(db).list(chatroom_id)}
    return RoomGuests(
        sessions=sessions,
        registered=registered,
        unaffiliated=frozenset(await _unaffiliated(db, chatroom_id, set(registered))),
    )


async def _unaffiliated(db: AsyncSession, chatroom_id: uuid.UUID, user_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not user_ids:
        return set()
    project_id = await _project_id(db, chatroom_id)
    # A room whose project chain is broken is unreadable to everyone, so there is
    # no one to mislead; marking every registered guest is the fail-closed reading.
    holders = (
        await TenancyRoleResolver(db).project_role_holders(list(user_ids), project_id=project_id)
        if project_id is not None
        else set()
    )
    admins = await IdentityFacade(db).admin_ids()
    return user_ids - holders - admins


async def _project_id(db: AsyncSession, chatroom_id: uuid.UUID) -> uuid.UUID | None:
    room = await ChatroomRepository(db).get(chatroom_id)
    if room is None:
        return None
    workspace = await WorkspaceRepository(db).get(room.workspace_id)
    return workspace.project_id if workspace is not None else None


__all__ = ["RoomGuests", "load_room_guests"]
