"""Chat-room use-cases (§22.10, R13.02 / R13.04 / R13.05)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import ChatroomNotFound, RoomAccessFlagsConflict
from contexts.conversation.domain.models import (
    Chatroom,
    ChatroomAgent,
    ChatroomAgentRole,
    ChatroomGuest,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomGuestRepository,
    ChatroomMemberGroupRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from shared_kernel import audit


@dataclass(frozen=True, slots=True)
class ChatroomFlagsPatch:
    name: str | None = None
    allow_org_members: bool | None = None
    allow_project_members: bool | None = None
    allow_project_owners_only: bool | None = None
    allow_guest_links: bool | None = None
    allow_member_groups: bool | None = None
    disclose_observers: bool | None = None
    # §32 ([R32.05]). Creator-only on the same terms as `disclose_observers`, and
    # gated at the route for the same reason: the capability check above it governs
    # the access flags, and disclosure is not one of them.
    disclose_drafts: bool | None = None


def _assert_flag_exclusivity(*, allow_member_groups: bool, allow_project_members: bool) -> None:
    """R13.04. Asserted here as well as at the route so a second caller cannot
    bypass it by reaching the service directly."""
    if allow_member_groups and allow_project_members:
        raise RoomAccessFlagsConflict("allow_member_groups and allow_project_members are mutually exclusive")


class ChatroomService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rooms = ChatroomRepository(db)
        self._workspaces = WorkspaceRepository(db)
        self._agents = ChatroomAgentRepository(db)
        self._guests = ChatroomGuestRepository(db)
        self._member_groups = ChatroomMemberGroupRepository(db)

    async def bound_group_ids(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
        return await self._member_groups.list_for_room(chatroom_id)

    async def set_bound_groups(
        self,
        *,
        chatroom_id: uuid.UUID,
        group_ids: Sequence[uuid.UUID],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> set[uuid.UUID]:
        """Replace this room's Member Group bindings (R13.29).

        The audit row carries both sides, because "who could read this room on
        that date" is a question the group list alone cannot answer after the fact.
        """
        before = await self._member_groups.list_for_room(chatroom_id)
        await self._member_groups.replace(chatroom_id=chatroom_id, group_ids=group_ids)
        after = await self._member_groups.list_for_room(chatroom_id)
        if before != after:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.member_groups_bound",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom",
                    resource_id=chatroom_id,
                    metadata={
                        "before": sorted(str(g) for g in before),
                        "after": sorted(str(g) for g in after),
                    },
                    request_id=request_id,
                ),
            )
        return after

    # ---- queries ---------------------------------------------------------

    async def get(self, chatroom_id: uuid.UUID) -> Chatroom:
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        return room

    async def list_agents(
        self,
        chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomAgent]:
        return await self._agents.list(chatroom_id)

    async def list_guests(
        self,
        chatroom_id: uuid.UUID,
    ) -> Sequence[ChatroomGuest]:
        return await self._guests.list(chatroom_id)

    async def rooms_with_observers(
        self,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        return await self._agents.rooms_with_observers(chatroom_ids)

    # ---- commands --------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        allow_org_members: bool = False,
        allow_project_members: bool = True,
        allow_project_owners_only: bool = False,
        allow_guest_links: bool = False,
        allow_member_groups: bool = False,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom:
        _assert_flag_exclusivity(
            allow_member_groups=allow_member_groups,
            allow_project_members=allow_project_members,
        )
        room = await self._rooms.create(
            workspace_id=workspace_id,
            name=name,
            allow_org_members=allow_org_members,
            allow_project_members=allow_project_members,
            allow_project_owners_only=allow_project_owners_only,
            allow_guest_links=allow_guest_links,
            allow_member_groups=allow_member_groups,
            created_by_user_id=actor_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=room.id,
                metadata={
                    "workspace_id": str(workspace_id),
                    "name": name,
                    "flags": {
                        "allow_org_members": allow_org_members,
                        "allow_project_members": allow_project_members,
                        "allow_project_owners_only": allow_project_owners_only,
                        "allow_guest_links": allow_guest_links,
                        "allow_member_groups": allow_member_groups,
                    },
                },
                request_id=request_id,
            ),
        )
        return room

    async def patch(
        self,
        *,
        chatroom_id: uuid.UUID,
        expected_version: int,
        patch: ChatroomFlagsPatch,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom:
        # R13.04's one refused pair, evaluated against the state the patch would
        # PRODUCE, not against the state it names. Checking only the fields present
        # in the body would let a two-step widening through: set the group tier in
        # one request, re-enable project members in the next, and neither request
        # ever names both.
        current = await self.get(chatroom_id)
        _assert_flag_exclusivity(
            allow_member_groups=(
                patch.allow_member_groups
                if patch.allow_member_groups is not None
                else current.allow_member_groups
            ),
            allow_project_members=(
                patch.allow_project_members
                if patch.allow_project_members is not None
                else current.allow_project_members
            ),
        )
        values: dict[str, object] = {}
        if patch.name is not None:
            values["name"] = patch.name
        if patch.allow_org_members is not None:
            values["allow_org_members"] = patch.allow_org_members
        if patch.allow_project_members is not None:
            values["allow_project_members"] = patch.allow_project_members
        if patch.allow_project_owners_only is not None:
            values["allow_project_owners_only"] = patch.allow_project_owners_only
        if patch.allow_guest_links is not None:
            values["allow_guest_links"] = patch.allow_guest_links
        if patch.allow_member_groups is not None:
            values["allow_member_groups"] = patch.allow_member_groups
        # The two disclosure flags are handled as a pair rather than as two copies of
        # one branch: each gets its own audit event ([R28.09], [R32.06]), and the
        # "old" value for both comes from the `current` row already read above. The
        # earlier form re-read the room here, which was the same unchanged row a
        # second time on every disclosure patch.
        disclosure_changes: list[tuple[str, bool, bool]] = []
        for field, old, new in (
            ("disclose_observers", current.disclose_observers, patch.disclose_observers),
            ("disclose_drafts", current.disclose_drafts, patch.disclose_drafts),
        ):
            if new is not None:
                values[field] = new
                if new != old:
                    disclosure_changes.append((field, old, new))
        if not values:
            # Nothing to change; return existing row as-is.
            return await self.get(chatroom_id)
        room = await self._rooms.update(
            chatroom_id=chatroom_id,
            expected_version=expected_version,
            values=values,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"changed": list(values.keys())},
                request_id=request_id,
            ),
        )
        for field, old, new in disclosure_changes:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    # Distinct actions rather than one event carrying the field name:
                    # an operator asking "when did this room stop telling people their
                    # unsent text is readable" must not have to filter one action's
                    # metadata to find it.
                    action=(
                        "chatroom.disclosure_changed"
                        if field == "disclose_observers"
                        else "chatroom.draft_disclosure_changed"
                    ),
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom",
                    resource_id=chatroom_id,
                    metadata={"old": old, "new": new},
                    request_id=request_id,
                ),
            )
        return room

    async def soft_delete(
        self,
        *,
        chatroom_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Chatroom | None:
        """Soft-delete. R13.02: if the workspace has no other active rooms
        left after this call, auto-create a default room so the invariant
        "every workspace has ≥ 1 chatroom" still holds."""
        room = await self._rooms.get(chatroom_id)
        if room is None:
            raise ChatroomNotFound(str(chatroom_id))
        await self._rooms.soft_delete(chatroom_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                request_id=request_id,
            ),
        )
        remaining = await self._rooms.count_active_in_workspace(room.workspace_id)
        if remaining == 0:
            default_room = await self._rooms.create(
                workspace_id=room.workspace_id,
                name="general",
                created_by_user_id=actor_user_id,
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.created",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom",
                    resource_id=default_room.id,
                    metadata={
                        "workspace_id": str(room.workspace_id),
                        "auto_created": True,
                        "reason": "last_room_deleted",
                    },
                    request_id=request_id,
                ),
            )
            return default_room
        return None

    async def admin_restore(
        self,
        *,
        chatroom_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted chatroom (R8.13): pure deleted_at clear,
        emitting admin.restore_resource. Any R13.02 default room auto-created when
        the last room was deleted is left in place (the >= 1 room invariant holds)."""
        restored = await self._rooms.restore(chatroom_id)
        if not restored:
            return False
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.restore_resource",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom",
                resource_id=chatroom_id,
                request_id=request_id,
            ),
        )
        return True

    # ---- compaction -------------------------------------------------------

    @staticmethod
    async def request_compaction(chatroom_id: uuid.UUID) -> None:
        """Record a one-shot compact intent (K.2) and enqueue a job.

        The value is an *epoch* token, not a presence marker. Compaction is
        scoped to the agent that produced it (R9.09), so this room-level action
        must fold once for each `context_mode=compact` agent bound to the room;
        each claims this epoch exactly once via its own marker key (see
        `turn_engine._consume_compact_flag`). Writing a token rather than
        resolving the bound agents here keeps the conversation context free of
        any dependency on agent configuration.
        """
        from shared_kernel.auth.clients import get_redis
        from shared_kernel.queue import enqueue as enqueue_job

        await get_redis().set(f"compact:pending:{chatroom_id}", uuid.uuid4().hex, ex=3600)
        await enqueue_job("compact_chatroom", str(chatroom_id))

    # ---- agent registry --------------------------------------------------

    async def add_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        role: ChatroomAgentRole = ChatroomAgentRole.NORMAL,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.add(chatroom_id=chatroom_id, agent_id=agent_id, role=role)
        action = "chatroom.observer_bound" if role is ChatroomAgentRole.OBSERVER else "chatroom.agent_added"
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={"agent_id": str(agent_id), "role": role.value},
                request_id=request_id,
            ),
        )

    # Bounded retry for the read-then-CAS-write loop below — generous relative
    # to how rarely two role-change requests for the same binding actually
    # race (a human clicking a settings dropdown), while keeping the loop
    # provably terminating.
    _SET_ROLE_MAX_ATTEMPTS = 5

    async def set_agent_role(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        role: ChatroomAgentRole,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """CAS the transition on the role actually observed (mirrors the
        observation-release CAS in observation_repo.py) so concurrent
        role-change requests can't both win and both audit-log the same
        old_role -> new_role transition when only one of them really
        happened. A losing attempt re-reads the fresh role and retries —
        it may discover the target role was already reached (no-op) or
        needs a different transition than the one it started with."""
        for _attempt in range(self._SET_ROLE_MAX_ATTEMPTS):
            old_role = await self._agents.role_of(chatroom_id=chatroom_id, agent_id=agent_id)
            if old_role is None:
                return False
            if old_role is role:
                return True
            won = await self._agents.set_role(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                expected_role=old_role,
                role=role,
            )
            if not won:
                continue  # lost the race — re-read and retry against the fresh value
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="chatroom.observer_role_changed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="chatroom_agent",
                    resource_id=chatroom_id,
                    metadata={
                        "agent_id": str(agent_id),
                        "old_role": old_role.value,
                        "new_role": role.value,
                    },
                    request_id=request_id,
                ),
            )
            return True
        return False

    async def set_agent_activity_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted: bool,
        activity_type_ids: Sequence[uuid.UUID],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Grant or revoke one bound agent's activity start/end authority ([R30.37]).

        Returns whether a binding was updated; ``False`` means the agent is not bound
        to this room and nothing was written.

        No CAS loop, unlike ``set_agent_role``: that one exists because the audit
        event names the *transition* (`old_role -> new_role`), which two racing
        writers off one stale read would both claim. This event records the state
        written, which is true of the winner whatever it raced with — last write
        wins, and the audit trail says so honestly.

        ``activity_type_ids`` must already be validated against the room's project by
        the caller: this context cannot resolve an activity type, and the route is the
        only layer that may reach across to the activities facade.
        """
        written = await self._agents.set_activity_grant(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
            granted=granted,
            activity_type_ids=activity_type_ids,
            granted_by_user_id=actor_user_id,
        )
        if not written:
            return False
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.agent_activity_grant_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={
                    "agent_id": str(agent_id),
                    "granted": granted,
                    # Recorded on a revoke as well, as the empty list it effectively
                    # becomes: the stored allowlist survives a revoke, and an event
                    # echoing the residue would read as authority the agent no longer
                    # holds.
                    "activity_type_ids": [str(i) for i in activity_type_ids] if granted else [],
                },
                request_id=request_id,
            ),
        )
        return True

    async def set_agent_draft_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Grant or revoke one bound agent's live-draft reading ([R32.03]).

        Returns whether a binding was updated; ``False`` means the agent is not bound
        to this room and nothing was written.

        No CAS loop, for the reason :meth:`set_agent_activity_grant` gives: the audit
        event records the state written rather than a transition, so last write wins
        and the trail says so honestly.

        Unlike its sibling this takes no allowlist. What a granted agent may actually
        read is decided per call by the activity type's own ``expose_payload_to_agent``
        and by the platform payload policy ([R32.04]) — the same two gates a submitted
        payload passes. A second list here would be a third gate the teacher had to
        keep in step with the first two, and the state where they disagreed would be a
        draft readable on looser terms than its own submission.
        """
        written = await self._agents.set_draft_grant(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
            granted=granted,
            granted_by_user_id=actor_user_id,
        )
        if not written:
            return False
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.agent_draft_grant_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                # No draft content and no participant ids, here or anywhere on this
                # trail ([R32.06]). What an operator needs from this row is who gave
                # which agent the authority, and when.
                metadata={"agent_id": str(agent_id), "granted": granted},
                request_id=request_id,
            ),
        )
        return True

    async def remove_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
        restrict_to_normal: bool = False,
    ) -> None:
        """Unbind an agent. ``restrict_to_normal`` (O-5): a non-creator unbind
        targets normal bindings only, so an observer binding is a silent no-op
        — the response is 204 either way, never revealing whether the target
        was a hidden observer. Audit is emitted only when a row was removed."""
        removed = await self._agents.remove(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
            only_role=ChatroomAgentRole.NORMAL if restrict_to_normal else None,
        )
        if not removed:
            return
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="chatroom.agent_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="chatroom_agent",
                resource_id=chatroom_id,
                metadata={"agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )


__all__ = ["ChatroomFlagsPatch", "ChatroomService"]
