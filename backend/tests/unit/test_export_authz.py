"""Permission-matrix row 19 (chat.export) narrowing — the negative tests.

Each assertion here describes a disclosure that was live before this suite
existed: any caller who could *read* a room received a complete archive of
every participant's messages, edit histories and attachment object paths.

Decided semantics (dossier 2026-07-22-chat-export-authz-and-polling, Q-1a/Q-2/Q-3):
a narrowed export contains the caller's own messages plus all agent and system
messages; other users' messages are excluded, and guests may not export at all.

Coverage seam: the SQL predicate itself is pinned by compiling the statement
(`TestRepositoryPredicate`), while the manifest-level exclusions
(`TestManifestExcludesOtherSenders`) run against a repository fake that applies
that same documented predicate. Together they cover authorization decision ->
query predicate -> serialized payload without needing a live database.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from contexts.conversation.application.access import RoomAccess, export_sender_scope
from contexts.conversation.application.chat_export_service import ChatExportService
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.domain.models import (
    AttachmentStatus,
    ExportSenderScope,
    Message,
    MessageAttachment,
    MessageEdit,
    ScanStatus,
    SenderType,
)
from contexts.conversation.infrastructure.repositories.message_repo import MessageRepository
from shared_kernel.auth.permissions import Principal, Role

_NOW = datetime(2026, 7, 24, 12, 0, 0)
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()
_CALLER = uuid.uuid4()
_OTHER = uuid.uuid4()
_AGENT = uuid.uuid4()


def _principal(user_id: uuid.UUID = _CALLER, *, is_admin: bool = False) -> Principal:
    return Principal(user_id=user_id, is_admin=is_admin, email_verified=True)


def _access(*, roles: frozenset[Role] = frozenset(), is_guest: bool = False) -> RoomAccess:
    room = MagicMock()
    room.name = "general"
    return RoomAccess(
        chatroom=room,
        project_id=uuid.uuid4(),
        roles=roles,
        is_guest=is_guest,
    )


def _message(
    *,
    sender_type: SenderType,
    sender_id: uuid.UUID | None,
    content: str,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        chatroom_id=_ROOM,
        sender_type=sender_type,
        sender_id=sender_id,
        content_md=content,
        metadata={},
        version=1,
        created_at=_NOW,
        edited_at=None,
    )


class TestExportSenderScope:
    """Row 19's cells, read through the matrix rather than restated."""

    def test_admin_is_unnarrowed(self) -> None:
        scope = export_sender_scope(_access(), principal=_principal(is_admin=True))
        assert scope is ExportSenderScope.ALL

    @pytest.mark.parametrize("role", [Role.ORG_OWNER, Role.PROJECT_OWNER])
    def test_owner_is_unnarrowed(self, role: Role) -> None:
        scope = export_sender_scope(_access(roles=frozenset({role})), principal=_principal())
        assert scope is ExportSenderScope.ALL

    @pytest.mark.parametrize("role", [Role.ORG_MEMBER, Role.PROJECT_MEMBER])
    def test_member_is_narrowed(self, role: Role) -> None:
        scope = export_sender_scope(_access(roles=frozenset({role})), principal=_principal())
        assert scope is ExportSenderScope.OWN_PLUS_NON_USER

    def test_guest_is_denied(self) -> None:
        """Q-2: the row has no GUEST cell, and the resolver never emits one."""
        with pytest.raises(ForbiddenInRoom):
            export_sender_scope(_access(is_guest=True), principal=_principal())

    def test_owner_role_wins_over_guest_enrolment(self) -> None:
        access = _access(roles=frozenset({Role.PROJECT_OWNER}), is_guest=True)
        assert export_sender_scope(access, principal=_principal()) is ExportSenderScope.ALL

    def test_member_who_is_also_a_guest_is_narrowed_not_denied(self) -> None:
        access = _access(roles=frozenset({Role.PROJECT_MEMBER}), is_guest=True)
        assert export_sender_scope(access, principal=_principal()) is ExportSenderScope.OWN_PLUS_NON_USER


class TestRepositoryPredicate:
    """AC-3: the narrowing is in the WHERE clause, not a post-filter."""

    @staticmethod
    async def _where_clause(**kwargs: object) -> str:
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result

        await MessageRepository(db).all_for_chatroom(_ROOM, **kwargs)  # type: ignore[arg-type]

        sql = str(
            db.execute.await_args_list[0]
            .args[0]
            .compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        # Assert on the predicate only: the SELECT list always names every column.
        return sql.split(" \nWHERE ", 1)[1]

    async def test_own_user_id_narrows_the_where_clause(self) -> None:
        where = await self._where_clause(own_user_id=_CALLER)
        assert "sender_type != 'user'" in where
        assert str(_CALLER) in where

    async def test_default_call_has_no_sender_predicate(self) -> None:
        """The moderator path must be byte-for-byte the previous behaviour."""
        where = await self._where_clause()
        assert "sender_type" not in where
        assert "sender_id" not in where


_ServiceTest = Callable[..., Awaitable[None]]


def _patched_service(fn: _ServiceTest) -> _ServiceTest:
    """Apply the nine collaborator patches every service test needs."""
    wrapped: Any = fn
    for decorator in (
        # Patch the emitter only, not the module: the assertions read a real
        # AuditEvent off the call.
        patch("shared_kernel.audit.emit", new_callable=AsyncMock),
        patch("contexts.conversation.application.chat_export_service.get_minio_client"),
        patch("contexts.conversation.application.chat_export_service.ChatroomRepository"),
        patch("contexts.conversation.application.chat_export_service.MessageAttachmentRepository"),
        patch("contexts.conversation.application.chat_export_service.MessageEditRepository"),
        patch("contexts.conversation.application.chat_export_service.MessageRepository"),
        patch("contexts.conversation.application.chat_export_service.ensure_can_read"),
        patch("contexts.conversation.application.chat_export_service.resolve_room_access"),
        patch("contexts.conversation.application.chat_export_service.IdentityFacade"),
    ):
        wrapped = decorator(wrapped)
    return cast(_ServiceTest, wrapped)


class _Harness:
    """Seeds a two-user room and records what the service asked the repo for."""

    def __init__(
        self,
        mocks: dict[str, MagicMock],
        *,
        messages: list[Message],
        edits: dict[uuid.UUID, list[MessageEdit]] | None = None,
        attachments: dict[uuid.UUID, list[MessageAttachment]] | None = None,
    ) -> None:
        self._all = messages
        self.msgs_repo = AsyncMock()
        self.msgs_repo.all_for_chatroom.side_effect = self._filter
        mocks["MockMsgs"].return_value = self.msgs_repo

        edits = edits or {}
        attachments = attachments or {}
        mocks["MockEdits"].return_value = AsyncMock(
            list_for_message=AsyncMock(side_effect=lambda mid: edits.get(mid, []))
        )
        mocks["MockAtts"].return_value = AsyncMock(
            list_for_message=AsyncMock(side_effect=lambda mid: attachments.get(mid, []))
        )

        room = MagicMock()
        room.name = "general"
        mocks["MockRooms"].return_value = AsyncMock(get=AsyncMock(return_value=room))

        self.minio = AsyncMock()
        self.minio.exports_bucket = "exports"
        mocks["mock_minio_fn"].return_value = self.minio

    async def _filter(self, chatroom_id: uuid.UUID, **kwargs: object) -> list[Message]:
        """Apply row 19's documented predicate, as the SQL does."""
        own = kwargs.get("own_user_id")
        if own is None:
            return list(self._all)
        return [m for m in self._all if m.sender_type is not SenderType.USER or m.sender_id == own]

    @property
    def own_user_id(self) -> object:
        return self.msgs_repo.all_for_chatroom.await_args.kwargs.get("own_user_id")

    def manifest(self) -> dict[str, object]:
        return json.loads(self.minio.put_object.call_args.kwargs["data"])


def _unpack(args: tuple[MagicMock, ...]) -> dict[str, MagicMock]:
    """Name the patch arguments. `patch` injects innermost-first, which is the
    order `_patched_service` applied them, not the order they are listed there."""
    names = (
        "mock_emit",
        "mock_minio_fn",
        "MockRooms",
        "MockAtts",
        "MockEdits",
        "MockMsgs",
        "mock_ensure",
        "mock_resolve",
        "MockIdentity",
    )
    return dict(zip(names, args, strict=True))


def _wire_access(mocks: dict[str, MagicMock], access: RoomAccess, *, is_admin: bool = False) -> None:
    mocks["MockIdentity"].return_value = AsyncMock(is_admin=AsyncMock(return_value=is_admin))
    mocks["mock_resolve"].return_value = access


class TestNarrowingReachesTheQuery:
    @_patched_service
    async def test_member_export_passes_own_user_id(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        harness = _Harness(mocks, messages=[])

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
        )

        assert harness.own_user_id == _CALLER

    @_patched_service
    async def test_owner_export_passes_no_sender_filter(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_OWNER})))
        harness = _Harness(mocks, messages=[])

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.ALL,
        )

        assert harness.own_user_id is None

    @_patched_service
    async def test_guest_export_is_denied(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(is_guest=True))
        _Harness(mocks, messages=[])

        with pytest.raises(ForbiddenInRoom):
            await ChatExportService(AsyncMock()).build_and_upload_export(
                job_id=_JOB,
                chatroom_id=_ROOM,
                owner_user_id=_CALLER,
                export_format="json",
                recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
            )


class TestManifestExcludesOtherSenders:
    @staticmethod
    def _room() -> tuple[Message, Message, Message, Message]:
        return (
            _message(sender_type=SenderType.USER, sender_id=_CALLER, content="mine"),
            _message(sender_type=SenderType.USER, sender_id=_OTHER, content="theirs"),
            _message(sender_type=SenderType.AGENT, sender_id=_AGENT, content="agent reply"),
            _message(sender_type=SenderType.SYSTEM, sender_id=None, content="joined"),
        )

    @_patched_service
    async def test_excludes_other_users_messages(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        mine, theirs, agent, system = self._room()
        harness = _Harness(mocks, messages=[mine, theirs, agent, system])

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
        )

        ids = {m["id"] for m in harness.manifest()["messages"]}  # type: ignore[index,union-attr]
        assert ids == {str(mine.id), str(agent.id), str(system.id)}, "Q-3: agent and system stay in"
        assert str(theirs.id) not in ids
        assert "theirs" not in json.dumps(harness.manifest())

    @_patched_service
    async def test_excludes_other_users_edit_history(self, *args: MagicMock) -> None:
        """The sharpest item in the payload: content a user deliberately revised away."""
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        mine, theirs, agent, system = self._room()
        harness = _Harness(
            mocks,
            messages=[mine, theirs, agent, system],
            edits={
                theirs.id: [
                    MessageEdit(
                        id=uuid.uuid4(),
                        message_id=theirs.id,
                        old_content_md="retracted-secret",
                        edited_by_user_id=_OTHER,
                        edited_at=_NOW,
                    )
                ]
            },
        )

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
        )

        assert "retracted-secret" not in json.dumps(harness.manifest())

    @_patched_service
    async def test_excludes_other_users_attachment_paths(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        mine, theirs, agent, system = self._room()
        harness = _Harness(
            mocks,
            messages=[mine, theirs, agent, system],
            attachments={
                theirs.id: [
                    MessageAttachment(
                        id=uuid.uuid4(),
                        message_id=theirs.id,
                        filename="private.pdf",
                        mime="application/pdf",
                        size_bytes=10,
                        minio_path="chat-uploads/other/private.pdf",
                        status=AttachmentStatus.ACTIVE,
                        scan_status=ScanStatus.CLEAN,
                        scan_at=_NOW,
                        expires_at=_NOW + timedelta(days=3),
                    )
                ]
            },
        )

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
        )

        payload = json.dumps(harness.manifest())
        assert "chat-uploads/other/private.pdf" not in payload
        assert "private.pdf" not in payload


class TestWorkerRefusesToWiden:
    @_patched_service
    async def test_rederived_scope_wider_than_recorded_fails_the_job(self, *args: MagicMock) -> None:
        """AC-6: Redis describes the export; the database decides it."""
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_OWNER})))
        _Harness(mocks, messages=[])

        with pytest.raises(PermissionError, match="wider"):
            await ChatExportService(AsyncMock()).build_and_upload_export(
                job_id=_JOB,
                chatroom_id=_ROOM,
                owner_user_id=_CALLER,
                export_format="json",
                recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
            )

    @_patched_service
    async def test_rederived_scope_narrower_than_recorded_is_honoured(self, *args: MagicMock) -> None:
        """Demotion between enqueue and execution narrows; it never fails closed."""
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        harness = _Harness(mocks, messages=[])

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.ALL,
        )

        assert harness.own_user_id == _CALLER


class TestExportAudit:
    @_patched_service
    async def test_emits_message_exported_without_content_or_url(self, *args: MagicMock) -> None:
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_MEMBER})))
        mine = _message(sender_type=SenderType.USER, sender_id=_CALLER, content="top-secret-body")
        harness = _Harness(mocks, messages=[mine])

        await ChatExportService(AsyncMock()).build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_CALLER,
            export_format="json",
            recorded_sender_scope=ExportSenderScope.OWN_PLUS_NON_USER,
        )

        emit = mocks["mock_emit"]
        emit.assert_awaited_once()
        event = emit.await_args.args[1]
        assert event.action == "message.exported"
        assert event.actor_user_id == _CALLER
        assert event.resource_id == _ROOM
        assert event.metadata["sender_scope"] == ExportSenderScope.OWN_PLUS_NON_USER.value
        assert event.metadata["export_format"] == "json"

        serialized = json.dumps(event.metadata, default=str)
        assert "top-secret-body" not in serialized
        assert "http" not in serialized
        harness.minio.presigned_get.assert_not_called()

    @_patched_service
    async def test_no_audit_when_upload_fails(self, *args: MagicMock) -> None:
        """The trail must never describe an export that was not delivered."""
        mocks = _unpack(args)
        _wire_access(mocks, _access(roles=frozenset({Role.PROJECT_OWNER})))
        harness = _Harness(mocks, messages=[])
        harness.minio.put_object.side_effect = RuntimeError("minio down")

        with pytest.raises(RuntimeError):
            await ChatExportService(AsyncMock()).build_and_upload_export(
                job_id=_JOB,
                chatroom_id=_ROOM,
                owner_user_id=_CALLER,
                export_format="json",
                recorded_sender_scope=ExportSenderScope.ALL,
            )

        mocks["mock_emit"].assert_not_awaited()
