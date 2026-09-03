"""The chatroom API's draft surfaces: the DTO, the gates, the grant route (§32).

Three things are pinned here, and the first two are the ones a later edit is most
likely to break without noticing:

- **The disclosure predicate.** `drafts_readable` is what a participant is actually
  told, and it is false whenever disclosure is off *regardless of a live grant*
  ([R32.05]) — so the field can never be used to detect that some agent holds the
  authority in a room whose creator switched the notice off. §10 records that as an
  accepted risk rather than a solved one; this is the half that is actually enforced.

- **The creator-only carve-out**, which grew a second field. `patch_chatroom` used to
  test `fields == {"disclose_observers"}` exactly; a `disclose_drafts`-only patch
  would have fallen through to the capability branch and demanded a permission
  [R32.05] says it must not. The subset form is asserted in both directions.

- **`may_read_drafts` is the creator's to see.** A non-creator is told *that* drafts
  are readable, never by which agent ([R28.10], [R32.05]).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from contexts.conversation.application.access import RoomAccess
from contexts.conversation.domain.errors import NotRoomCreator
from contexts.conversation.domain.models import ChatroomAgentRole
from shared_kernel.auth.permissions import Role
from tests.unit.chatroom_fakes import chatroom_row

_CTX = SimpleNamespace(actor_ip=None, request_id=None)


def _committing_db() -> SimpleNamespace:
    """A db double for the writers that commit before publishing.

    `disclose_drafts` is one of `_DISCLOSURE_FIELDS`, so a draft-disclosure patch
    takes the same `chatroom.updated` path a `disclose_observers` patch does — and
    it should, since `drafts_readable` is a viewer-visible DTO field that has just
    changed for everyone in the room ([R32.05]).
    """
    return SimpleNamespace(commit=AsyncMock())


# Creator authority requires *current* membership as well as the created_by match
# (`is_room_creator`, and O-7 of the observer dossier: a creator removed from the
# project loses it). A bare frozenset() would therefore fail every gate below for
# the wrong reason.
_MEMBER = frozenset({Role.PROJECT_MEMBER})


def _principal(user_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id or uuid.uuid4(), is_admin=False)


def _access(*, created_by: uuid.UUID, roles: frozenset[Role] = _MEMBER) -> RoomAccess:
    return RoomAccess(
        chatroom=SimpleNamespace(created_by_user_id=created_by),
        project_id=uuid.uuid4(),
        roles=roles,
        is_guest=False,
    )


class TestTheDisclosurePredicate:
    """AC-11's DTO half."""

    def _out(self, *, disclose: bool, has_readers: bool, guest: bool = False) -> Any:
        import app.api.v1.chatrooms as chatrooms_mod

        return chatrooms_mod._to_out(
            chatroom_row(created_by=uuid.uuid4(), disclose_drafts=disclose),
            has_draft_readers=has_readers,
            viewer_is_pure_guest=guest,
        )

    def test_a_granted_room_with_disclosure_on_reports_readable(self) -> None:
        out = self._out(disclose=True, has_readers=True)

        assert out.disclose_drafts is True
        assert out.drafts_readable is True

    def test_disclosure_off_hides_a_live_grant(self) -> None:
        """The accepted risk of §10, and the behaviour that makes it one: a creator
        who turns the notice off produces a room where unsent text is read and
        nobody is told. The DTO must not leak it back through a side channel."""
        out = self._out(disclose=False, has_readers=True)

        assert out.disclose_drafts is False
        assert out.drafts_readable is False

    def test_disclosure_on_with_no_grant_reports_nothing_readable(self) -> None:
        """The chip must not appear in a room where no agent may read anything — it
        would teach participants to ignore it in the rooms where it matters."""
        out = self._out(disclose=True, has_readers=False)

        assert out.drafts_readable is False

    def test_a_guest_is_told_on_the_same_terms_as_a_member(self) -> None:
        """§8: a guest's unsent text is reported and readable exactly like a
        member's, so suppressing the chip would withhold the disclosure from the
        person it is about. This is the one place §32 does not copy [R28.02]'s guest
        neutralisation, and the difference is deliberate — a guest still learns
        nothing about *which* agent holds the grant.
        """
        member = self._out(disclose=True, has_readers=True)
        guest = self._out(disclose=True, has_readers=True, guest=True)

        assert guest.drafts_readable == member.drafts_readable is True
        # And the observer fields beside them are still neutralised, so this is a
        # deliberate difference rather than a guest suppression that was forgotten.
        assert guest.disclose_observers is False
        assert guest.observers_present is False

    def test_a_guest_in_a_room_with_disclosure_off_is_told_nothing(self) -> None:
        guest = self._out(disclose=False, has_readers=True, guest=True)

        assert guest.drafts_readable is False


class TestWhichAgentIsTheCreatorsToKnow:
    """[R32.05] / [R28.10] over the agent listing."""

    async def _list(self, monkeypatch: pytest.MonkeyPatch, *, creator: bool) -> list[Any]:
        import app.api.v1.chatrooms as chatrooms_mod

        uid = uuid.uuid4()
        access = _access(created_by=uid if creator else uuid.uuid4())

        async def _resolve(db: Any, *, principal: Any, chatroom_id: Any) -> RoomAccess:
            return access

        class _Service:
            def __init__(self, db: Any) -> None:
                pass

            async def list_agents(self, chatroom_id: Any) -> list[Any]:
                return [
                    SimpleNamespace(
                        agent_id=uuid.uuid4(),
                        role=ChatroomAgentRole.NORMAL,
                        may_control_activities=False,
                        activity_type_allowlist=(),
                        may_read_drafts=True,
                    )
                ]

        monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
        monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
        return await chatrooms_mod.list_chatroom_agents(
            chatroom_id=uuid.uuid4(),
            pagination=SimpleNamespace(offset=0, limit=50),
            principal=_principal(uid),
            db=object(),
        )

    async def test_the_creator_sees_which_binding_holds_the_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = await self._list(monkeypatch, creator=True)

        assert rows[0].may_read_drafts is True

    async def test_a_non_creator_is_told_nothing_about_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`None` is dropped by `response_model_exclude_none`, so a non-creator's
        response is shape-identical to the pre-grant API rather than carrying a
        `false` that could be compared against the room's chip to identify the
        holder by elimination."""
        rows = await self._list(monkeypatch, creator=False)

        assert rows[0].may_read_drafts is None


def _wire_patch(monkeypatch: pytest.MonkeyPatch, *, access: RoomAccess, cap_calls: list[Any]) -> Any:
    import app.api.v1.chatrooms as chatrooms_mod

    async def _pid(db: Any, chatroom_id: Any) -> uuid.UUID:
        return uuid.uuid4()

    async def _cap(db: Any, principal: Any, project_id: Any, capability: Any) -> None:
        cap_calls.append(capability)

    async def _resolve(db: Any, *, principal: Any, chatroom_id: Any) -> RoomAccess:
        return access

    class _Service:
        def __init__(self, db: Any) -> None:
            pass

        async def patch(self, **kw: Any) -> Any:
            return chatroom_row()

        async def rooms_with_observers(self, ids: Any) -> set[uuid.UUID]:
            return set()

        async def rooms_with_draft_readers(self, ids: Any) -> set[uuid.UUID]:
            return set()

    monkeypatch.setattr(chatrooms_mod, "_project_id_for_chatroom", _pid)
    monkeypatch.setattr(chatrooms_mod, "_require_project_cap", _cap)
    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)
    return chatrooms_mod


class TestTheCreatorOnlyCarveOut:
    """AC-12. The gate the second disclosure field had to be folded into."""

    async def test_a_draft_disclosure_only_patch_needs_no_capability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[R32.05]: "may do so without holding RESOURCE_CREATE_EDIT". The exact-set
        test this replaced would have sent this patch down the capability branch."""
        uid = uuid.uuid4()
        cap_calls: list[Any] = []
        mod = _wire_patch(monkeypatch, access=_access(created_by=uid), cap_calls=cap_calls)

        await mod.patch_chatroom(
            mod.ChatroomPatchIn(disclose_drafts=False),
            chatroom_id=uuid.uuid4(),
            if_match="1",
            ctx=_CTX,
            principal=_principal(uid),
            db=_committing_db(),
        )

        assert cap_calls == []

    async def test_both_disclosure_fields_together_still_need_no_capability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The subset test, at its widest legal input."""
        uid = uuid.uuid4()
        cap_calls: list[Any] = []
        mod = _wire_patch(monkeypatch, access=_access(created_by=uid), cap_calls=cap_calls)

        await mod.patch_chatroom(
            mod.ChatroomPatchIn(disclose_drafts=False, disclose_observers=False),
            chatroom_id=uuid.uuid4(),
            if_match="1",
            ctx=_CTX,
            principal=_principal(uid),
            db=_committing_db(),
        )

        assert cap_calls == []

    async def test_a_non_creator_cannot_change_draft_disclosure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "and by nobody else" (AC-12). A project owner who did not create the room
        is still refused."""
        cap_calls: list[Any] = []
        mod = _wire_patch(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER})),
            cap_calls=cap_calls,
        )

        with pytest.raises(NotRoomCreator):
            await mod.patch_chatroom(
                mod.ChatroomPatchIn(disclose_drafts=False),
                chatroom_id=uuid.uuid4(),
                if_match="1",
                ctx=_CTX,
                principal=_principal(),
                db=_committing_db(),
            )

    async def test_a_mixed_patch_keeps_both_gates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A flag change alongside a disclosure change must satisfy the capability
        check *and* the creator check — the subset test must not widen the carve-out
        to any patch that merely mentions a disclosure field."""
        cap_calls: list[Any] = []
        mod = _wire_patch(
            monkeypatch,
            access=_access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER})),
            cap_calls=cap_calls,
        )

        with pytest.raises(NotRoomCreator):
            await mod.patch_chatroom(
                mod.ChatroomPatchIn(name="renamed", disclose_drafts=False),
                chatroom_id=uuid.uuid4(),
                if_match="1",
                ctx=_CTX,
                principal=_principal(),
                db=_committing_db(),
            )

        assert len(cap_calls) == 1


class TestTheGrantRoute:
    async def _call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        access: RoomAccess,
        written: bool,
        granted: bool = True,
    ) -> list[Any]:
        import app.api.v1.chatrooms as chatrooms_mod

        calls: list[Any] = []

        async def _resolve(db: Any, *, principal: Any, chatroom_id: Any) -> RoomAccess:
            return access

        class _Facade:
            def __init__(self, db: Any) -> None:
                pass

            async def set_agent_draft_grant(self, **kw: Any) -> bool:
                calls.append(kw)
                return written

        monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
        monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Facade)

        class _Db:
            async def commit(self) -> None:
                calls.append("commit")

        await chatrooms_mod.patch_chatroom_agent_draft_access(
            chatrooms_mod.AgentDraftAccessIn(granted=granted),
            chatroom_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            ctx=_CTX,
            principal=_principal(access.chatroom.created_by_user_id),
            db=_Db(),
        )
        return calls

    async def test_the_creator_may_grant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid.uuid4()

        calls = await self._call(monkeypatch, access=_access(created_by=uid), written=True)

        assert calls[0]["granted"] is True
        assert calls[0]["actor_user_id"] == uid
        assert calls[-1] == "commit"

    async def test_a_non_creator_is_refused_before_anything_is_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-2's authority half. `ensure_room_creator` runs before the facade call,
        so a refusal cannot leave a partially written grant behind."""
        import app.api.v1.chatrooms as chatrooms_mod

        calls: list[Any] = []
        access = _access(created_by=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}))

        async def _resolve(db: Any, *, principal: Any, chatroom_id: Any) -> RoomAccess:
            return access

        class _Facade:
            def __init__(self, db: Any) -> None:
                pass

            async def set_agent_draft_grant(self, **kw: Any) -> bool:
                calls.append(kw)
                return True

        monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
        monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Facade)

        with pytest.raises(NotRoomCreator):
            await chatrooms_mod.patch_chatroom_agent_draft_access(
                chatrooms_mod.AgentDraftAccessIn(granted=True),
                chatroom_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                ctx=_CTX,
                principal=_principal(),
                db=_committing_db(),
            )

        assert calls == []

    async def test_an_unbound_agent_is_a_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import HTTPException

        uid = uuid.uuid4()

        with pytest.raises(HTTPException) as excinfo:
            await self._call(monkeypatch, access=_access(created_by=uid), written=False)

        assert excinfo.value.status_code == 404

    async def test_a_revoke_takes_the_same_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid.uuid4()

        calls = await self._call(monkeypatch, access=_access(created_by=uid), written=True, granted=False)

        assert calls[0]["granted"] is False

    def test_the_grant_body_carries_no_allowlist(self) -> None:
        """Deliberate, and worth pinning: adding one here would create a third gate
        beside the two read-time ones ([R32.04]), and the state where they disagreed
        would be a draft readable on looser terms than its own submission."""
        import app.api.v1.chatrooms as chatrooms_mod

        assert set(chatrooms_mod.AgentDraftAccessIn.model_fields) == {"granted"}
