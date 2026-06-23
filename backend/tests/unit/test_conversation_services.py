"""Unit tests for conversation services: ChatroomService, MessageService,
AttachmentService (upload/scan/bind), export_service, ChatExportService.

Covers: chatroom CRUD + auto-create-on-last-delete invariant, message
send/edit/delete with 5-min window + moderator paths, attachment single-shot
+ tus + quarantine guard + download MIME gating, export state machine,
chat export ACL + manifest build.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application.attachment_service import (
    AttachmentPointer,
    AttachmentService,
)
from contexts.conversation.application.chatroom_service import (
    ChatroomFlagsPatch,
    ChatroomService,
)
from contexts.conversation.application.message_service import (
    SELF_EDIT_WINDOW,
    EditAuthority,
    MessageService,
)
from contexts.conversation.domain.errors import (
    AttachmentNotFound,
    AttachmentQuarantined,
    AttachmentTooLarge,
    ChatroomNotFound,
    MessageEditWindowExceeded,
    MessageImmutable,
    MessageNotFound,
)
from contexts.conversation.domain.models import (
    AttachmentStatus,
    Chatroom,
    Message,
    MessageAttachment,
    ScanStatus,
    SenderType,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0)
_USER = uuid.uuid4()
_AGENT = uuid.uuid4()
_ROOM = uuid.uuid4()
_WS = uuid.uuid4()
_PROJECT = uuid.uuid4()
_MSG = uuid.uuid4()


def _chatroom(*, room_id: uuid.UUID | None = None, version: int = 1) -> Chatroom:
    return Chatroom(
        id=room_id or _ROOM,
        workspace_id=_WS,
        name="general",
        allow_org_members=False,
        allow_project_members=True,
        allow_project_owners_only=False,
        allow_guest_links=False,
        guest_token="tok",
        version=version,
        created_at=_NOW,
        deleted_at=None,
    )


def _message(
    *,
    msg_id: uuid.UUID | None = None,
    sender: SenderType = SenderType.USER,
    sender_id: uuid.UUID | None = None,
    version: int = 1,
    created_at: datetime | None = None,
) -> Message:
    return Message(
        id=msg_id or _MSG,
        chatroom_id=_ROOM,
        sender_type=sender,
        sender_id=sender_id or _USER,
        content_md="hello",
        version=version,
        created_at=created_at or _NOW,
    )


def _attachment(
    *,
    att_id: uuid.UUID | None = None,
    status: AttachmentStatus = AttachmentStatus.ACTIVE,
    mime: str = "image/png",
) -> MessageAttachment:
    return MessageAttachment(
        id=att_id or uuid.uuid4(),
        message_id=_MSG,
        filename="pic.png",
        mime=mime,
        size_bytes=1024,
        minio_path="chat-uploads/key/pic.png",
        status=status,
        scan_status=ScanStatus.CLEAN,
        scan_at=_NOW,
        expires_at=_NOW + timedelta(days=3),
        chatroom_id=_ROOM,
        uploaded_by_user_id=_USER,
    )


def _make_chatroom_service(
    *,
    rooms: AsyncMock | None = None,
    workspaces: AsyncMock | None = None,
    agents: AsyncMock | None = None,
    guests: AsyncMock | None = None,
) -> ChatroomService:
    db = AsyncMock()
    svc = ChatroomService(db)
    if rooms is not None:
        svc._rooms = rooms
    if workspaces is not None:
        svc._workspaces = workspaces
    if agents is not None:
        svc._agents = agents
    if guests is not None:
        svc._guests = guests
    return svc


def _make_message_service(
    *,
    messages: AsyncMock | None = None,
    edits: AsyncMock | None = None,
    attachments: AsyncMock | None = None,
) -> MessageService:
    db = AsyncMock()
    svc = MessageService(db)
    if messages is not None:
        svc._messages = messages
    if edits is not None:
        svc._edits = edits
    if attachments is not None:
        svc._attachments = attachments
    return svc


def _make_attachment_service(
    *,
    repo: AsyncMock | None = None,
    minio: AsyncMock | None = None,
) -> AttachmentService:
    db = AsyncMock()
    svc = AttachmentService(db, minio=minio or AsyncMock())
    if repo is not None:
        svc._repo = repo
    return svc


# ===========================================================================
# ChatroomService
# ===========================================================================


class TestChatroomGet:
    async def test_found(self) -> None:
        rooms = AsyncMock()
        rooms.get.return_value = _chatroom()
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.get(_ROOM)
        assert result.id == _ROOM

    async def test_not_found(self) -> None:
        rooms = AsyncMock()
        rooms.get.return_value = None
        svc = _make_chatroom_service(rooms=rooms)

        with pytest.raises(ChatroomNotFound):
            await svc.get(uuid.uuid4())


class TestChatroomList:
    async def test_list_for_workspace(self) -> None:
        rooms = AsyncMock()
        rooms.list_for_workspace.return_value = [_chatroom()]
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.list_for_workspace(_WS)
        assert len(result) == 1
        rooms.list_for_workspace.assert_awaited_once()


class TestChatroomCreate:
    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_creates_and_audits(self, _audit) -> None:
        room = _chatroom()
        rooms = AsyncMock()
        rooms.create.return_value = room
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.create(
            workspace_id=_WS,
            name="general",
            actor_user_id=_USER,
            actor_ip="1.2.3.4",
        )

        assert result.id == room.id
        rooms.create.assert_awaited_once()
        _audit.assert_awaited_once()
        assert _audit.call_args[0][1].action == "chatroom.created"


class TestChatroomPatch:
    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_applies_changes(self, _audit) -> None:
        updated = _chatroom(version=2)
        rooms = AsyncMock()
        rooms.update.return_value = updated
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.patch(
            chatroom_id=_ROOM,
            expected_version=1,
            patch=ChatroomFlagsPatch(name="renamed"),
            actor_user_id=_USER,
            actor_ip=None,
        )

        assert result.version == 2
        rooms.update.assert_awaited_once()

    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_empty_patch_returns_existing(self, _audit) -> None:
        rooms = AsyncMock()
        rooms.get.return_value = _chatroom()
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.patch(
            chatroom_id=_ROOM,
            expected_version=1,
            patch=ChatroomFlagsPatch(),
            actor_user_id=_USER,
            actor_ip=None,
        )

        assert result.id == _ROOM
        rooms.update.assert_not_awaited()


class TestChatroomSoftDelete:
    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_not_found_raises(self, _audit) -> None:
        rooms = AsyncMock()
        rooms.get.return_value = None
        svc = _make_chatroom_service(rooms=rooms)

        with pytest.raises(ChatroomNotFound):
            await svc.soft_delete(chatroom_id=uuid.uuid4(), actor_user_id=_USER, actor_ip=None)

    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_auto_creates_default_when_last_room(self, _audit) -> None:
        room = _chatroom()
        default_room = _chatroom(room_id=uuid.uuid4())
        rooms = AsyncMock()
        rooms.get.return_value = room
        rooms.count_active_in_workspace.return_value = 0
        rooms.create.return_value = default_room
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.soft_delete(
            chatroom_id=_ROOM, actor_user_id=_USER, actor_ip=None
        )

        assert result is not None
        assert result.id == default_room.id
        rooms.create.assert_awaited_once()
        create_kwargs = rooms.create.call_args.kwargs
        assert create_kwargs["name"] == "general"
        assert _audit.await_count == 2

    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_no_auto_create_when_rooms_remain(self, _audit) -> None:
        rooms = AsyncMock()
        rooms.get.return_value = _chatroom()
        rooms.count_active_in_workspace.return_value = 2
        svc = _make_chatroom_service(rooms=rooms)

        result = await svc.soft_delete(
            chatroom_id=_ROOM, actor_user_id=_USER, actor_ip=None
        )

        assert result is None
        rooms.create.assert_not_awaited()


class TestChatroomAgentRegistry:
    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_add_agent(self, _audit) -> None:
        agents_repo = AsyncMock()
        svc = _make_chatroom_service(agents=agents_repo)

        await svc.add_agent(
            chatroom_id=_ROOM, agent_id=_AGENT,
            actor_user_id=_USER, actor_ip=None,
        )

        agents_repo.add.assert_awaited_once()
        assert _audit.call_args[0][1].action == "chatroom.agent_added"

    @patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)
    async def test_remove_agent(self, _audit) -> None:
        agents_repo = AsyncMock()
        svc = _make_chatroom_service(agents=agents_repo)

        await svc.remove_agent(
            chatroom_id=_ROOM, agent_id=_AGENT,
            actor_user_id=_USER, actor_ip=None,
        )

        agents_repo.remove.assert_awaited_once()
        assert _audit.call_args[0][1].action == "chatroom.agent_removed"


# ===========================================================================
# MessageService
# ===========================================================================


class TestMessageGet:
    async def test_found(self) -> None:
        msgs = AsyncMock()
        msgs.get.return_value = _message()
        svc = _make_message_service(messages=msgs)

        result = await svc.get(_MSG)
        assert result.id == _MSG

    async def test_not_found(self) -> None:
        msgs = AsyncMock()
        msgs.get.return_value = None
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageNotFound):
            await svc.get(uuid.uuid4())


class TestMessageList:
    async def test_clamps_limit(self) -> None:
        msgs = AsyncMock()
        msgs.list.return_value = []
        svc = _make_message_service(messages=msgs)

        await svc.list(chatroom_id=_ROOM, before=None, since=None, limit=999)
        call_kwargs = msgs.list.call_args.kwargs
        assert call_kwargs["limit"] == 200

    async def test_clamps_limit_below_one(self) -> None:
        msgs = AsyncMock()
        msgs.list.return_value = []
        svc = _make_message_service(messages=msgs)

        await svc.list(chatroom_id=_ROOM, before=None, since=None, limit=-5)
        call_kwargs = msgs.list.call_args.kwargs
        assert call_kwargs["limit"] == 1


class TestMessageSend:
    @patch("contexts.conversation.application.message_service.Publisher")
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    async def test_send_user_message(self, _audit, _pub_cls) -> None:
        msg = _message()
        msgs = AsyncMock()
        msgs.create.return_value = msg
        atts = AsyncMock()
        atts.bind_to_message.return_value = 0
        _pub_cls.return_value = AsyncMock()
        svc = _make_message_service(messages=msgs, attachments=atts)

        result = await svc.send(
            chatroom_id=_ROOM,
            sender_user_id=_USER,
            content_md="hello",
            actor_ip=None,
        )

        assert result.id == msg.id
        msgs.create.assert_awaited_once()
        create_kwargs = msgs.create.call_args.kwargs
        assert create_kwargs["sender_type"] is SenderType.USER

    @patch("contexts.conversation.application.message_service.Publisher")
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    async def test_send_with_attachments(self, _audit, _pub_cls) -> None:
        msg = _message()
        msgs = AsyncMock()
        msgs.create.return_value = msg
        atts = AsyncMock()
        atts.bind_to_message.return_value = 2
        att_ids = [uuid.uuid4(), uuid.uuid4()]
        _pub_cls.return_value = AsyncMock()
        svc = _make_message_service(messages=msgs, attachments=atts)

        await svc.send(
            chatroom_id=_ROOM,
            sender_user_id=_USER,
            content_md="see attached",
            attachment_ids=att_ids,
            actor_ip=None,
        )

        atts.bind_to_message.assert_awaited_once()
        audit_meta = _audit.call_args[0][1].metadata
        assert audit_meta["attachments_bound"] == 2
        assert audit_meta["attachments_requested"] == 2


class TestMessageSendAgent:
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    async def test_send_agent_message(self, _audit) -> None:
        msg = _message(sender=SenderType.AGENT, sender_id=_AGENT)
        msgs = AsyncMock()
        msgs.create.return_value = msg
        svc = _make_message_service(messages=msgs)

        result = await svc.send_agent(
            chatroom_id=_ROOM,
            agent_id=_AGENT,
            content_md="agent reply",
        )

        assert result.sender_type is SenderType.AGENT
        create_kwargs = msgs.create.call_args.kwargs
        assert create_kwargs["metadata"]["type"] == "agent_reply"


class TestMessageEdit:
    @patch("contexts.conversation.application.message_service.Publisher")
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.message_service.now", return_value=_NOW)
    async def test_self_edit_within_window(self, _now, _audit, _pub_cls) -> None:
        existing = _message(created_at=_NOW)
        updated = _message(version=2)
        msgs = AsyncMock()
        msgs.get.return_value = existing
        msgs.update_content.return_value = updated
        edits = AsyncMock()
        _pub_cls.return_value = AsyncMock()
        svc = _make_message_service(messages=msgs, edits=edits)

        result = await svc.edit(
            message_id=_MSG,
            expected_version=1,
            new_content_md="edited",
            authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
            actor_ip=None,
        )

        assert result.version == 2
        edits.record.assert_awaited_once()
        assert _audit.call_args[0][1].action == "message.edited"
        update_kwargs = msgs.update_content.call_args.kwargs
        assert update_kwargs["max_age"] == SELF_EDIT_WINDOW

    @patch("contexts.conversation.application.message_service.now")
    async def test_self_edit_past_window_raises(self, _now) -> None:
        _now.return_value = _NOW + SELF_EDIT_WINDOW + timedelta(seconds=1)
        existing = _message(created_at=_NOW)
        msgs = AsyncMock()
        msgs.get.return_value = existing
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageEditWindowExceeded):
            await svc.edit(
                message_id=_MSG,
                expected_version=1,
                new_content_md="too late",
                authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
                actor_ip=None,
            )

    async def test_edit_agent_message_by_non_moderator_raises(self) -> None:
        existing = _message(sender=SenderType.AGENT, sender_id=_AGENT)
        msgs = AsyncMock()
        msgs.get.return_value = existing
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageImmutable, match="R13.22"):
            await svc.edit(
                message_id=_MSG,
                expected_version=1,
                new_content_md="nope",
                authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
                actor_ip=None,
            )

    async def test_edit_not_author_raises(self) -> None:
        existing = _message(sender_id=uuid.uuid4())
        msgs = AsyncMock()
        msgs.get.return_value = existing
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageImmutable, match="not the author"):
            await svc.edit(
                message_id=_MSG,
                expected_version=1,
                new_content_md="nope",
                authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
                actor_ip=None,
            )

    @patch("contexts.conversation.application.message_service.Publisher")
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.message_service.now", return_value=_NOW + timedelta(hours=1))
    async def test_moderator_can_edit_past_window(self, _now, _audit, _pub_cls) -> None:
        other_user = uuid.uuid4()
        existing = _message(sender_id=other_user, created_at=_NOW)
        updated = _message(version=2)
        msgs = AsyncMock()
        msgs.get.return_value = existing
        msgs.update_content.return_value = updated
        edits = AsyncMock()
        _pub_cls.return_value = AsyncMock()
        svc = _make_message_service(messages=msgs, edits=edits)

        result = await svc.edit(
            message_id=_MSG,
            expected_version=1,
            new_content_md="moderator edit",
            authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=True),
            actor_ip=None,
        )

        assert result.version == 2
        assert _audit.call_args[0][1].action == "message.edited_by_moderator"
        update_kwargs = msgs.update_content.call_args.kwargs
        assert update_kwargs["max_age"] is None

    async def test_edit_not_found(self) -> None:
        msgs = AsyncMock()
        msgs.get.return_value = None
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageNotFound):
            await svc.edit(
                message_id=uuid.uuid4(),
                expected_version=1,
                new_content_md="nope",
                authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
                actor_ip=None,
            )


class TestMessageDelete:
    @patch("contexts.conversation.application.message_service.Publisher")
    @patch("contexts.conversation.application.message_service.audit.emit", new_callable=AsyncMock)
    async def test_delete_success(self, _audit, _pub_cls) -> None:
        existing = _message()
        msgs = AsyncMock()
        msgs.get.return_value = existing
        msgs.hard_delete.return_value = 1
        _pub_cls.return_value = AsyncMock()
        svc = _make_message_service(messages=msgs)

        await svc.delete(
            message_id=_MSG,
            authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
            actor_ip=None,
        )

        msgs.hard_delete.assert_awaited_once_with(_MSG)
        assert _audit.call_args[0][1].action == "message.deleted"

    async def test_delete_not_found(self) -> None:
        msgs = AsyncMock()
        msgs.get.return_value = None
        svc = _make_message_service(messages=msgs)

        with pytest.raises(MessageNotFound):
            await svc.delete(
                message_id=uuid.uuid4(),
                authority=EditAuthority(actor_user_id=_USER, is_admin=False, is_moderator=False),
                actor_ip=None,
            )


# ===========================================================================
# AttachmentService
# ===========================================================================


class TestAttachmentSingleShot:
    @patch("contexts.conversation.application.attachment_service._enqueue_scan", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.now", return_value=_NOW)
    async def test_ingest_success(self, _now, _audit, _scan) -> None:
        att = _attachment()
        repo = AsyncMock()
        repo.create.return_value = att
        minio = AsyncMock()
        minio.chat_uploads_bucket = "chat-uploads"
        svc = _make_attachment_service(repo=repo, minio=minio)

        result = await svc.ingest_single_shot(
            project_id=_PROJECT,
            chatroom_id=_ROOM,
            uploader_user_id=_USER,
            filename="pic.png",
            mime="image/png",
            data=b"x" * 100,
            actor_ip=None,
            request_id=None,
        )

        assert result.id == att.id
        minio.put_object.assert_awaited_once()
        repo.create.assert_awaited_once()
        _scan.assert_awaited_once()

    @patch("contexts.conversation.application.attachment_service.SINGLE_SHOT_MAX_BYTES", 1024)
    async def test_ingest_too_large_raises(self) -> None:
        svc = _make_attachment_service()

        with pytest.raises(AttachmentTooLarge):
            await svc.ingest_single_shot(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                uploader_user_id=_USER,
                filename="huge.bin",
                mime="application/octet-stream",
                data=b"x" * 1025,
                actor_ip=None,
                request_id=None,
            )


class TestAttachmentFinalizeTus:
    @patch("contexts.conversation.application.attachment_service._enqueue_scan", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.now", return_value=_NOW)
    async def test_finalize_tus_success(self, _now, _audit, _scan) -> None:
        att = _attachment()
        repo = AsyncMock()
        repo.create.return_value = att
        minio = AsyncMock()
        minio.chat_uploads_bucket = "chat-uploads"
        svc = _make_attachment_service(repo=repo, minio=minio)

        result = await svc.finalize_tus(
            attachment_id=att.id,
            project_id=_PROJECT,
            chatroom_id=_ROOM,
            uploader_user_id=_USER,
            filename="video.mp4",
            mime="video/mp4",
            staging_path="/tmp/staging/abc",
            size_bytes=50_000_000,
            actor_ip=None,
            request_id=None,
        )

        assert result.id == att.id
        minio.put_file.assert_awaited_once()


class TestAttachmentDownload:
    async def test_not_found_raises(self) -> None:
        repo = AsyncMock()
        repo.get.return_value = None
        svc = _make_attachment_service(repo=repo)

        with pytest.raises(AttachmentNotFound):
            await svc.get_for_download(attachment_id=uuid.uuid4())

    async def test_quarantined_raises(self) -> None:
        att = _attachment(status=AttachmentStatus.QUARANTINED)
        repo = AsyncMock()
        repo.get.return_value = att
        svc = _make_attachment_service(repo=repo)

        with pytest.raises(AttachmentQuarantined):
            await svc.get_for_download(attachment_id=att.id)

    async def test_inline_safe_mime(self) -> None:
        att = _attachment(mime="image/png")
        repo = AsyncMock()
        repo.get.return_value = att
        minio = AsyncMock()
        minio.presigned_get.return_value = "https://minio/signed"
        svc = _make_attachment_service(repo=repo, minio=minio)

        result = await svc.get_for_download(attachment_id=att.id)

        assert isinstance(result, AttachmentPointer)
        assert result.url == "https://minio/signed"
        call_kwargs = minio.presigned_get.call_args.kwargs
        assert call_kwargs["response_content_type"] == "image/png"
        assert "inline" in call_kwargs["response_content_disposition"]

    async def test_scriptable_mime_forced_download(self) -> None:
        att = _attachment(mime="text/html")
        repo = AsyncMock()
        repo.get.return_value = att
        minio = AsyncMock()
        minio.presigned_get.return_value = "https://minio/signed"
        svc = _make_attachment_service(repo=repo, minio=minio)

        await svc.get_for_download(attachment_id=att.id)

        call_kwargs = minio.presigned_get.call_args.kwargs
        assert call_kwargs["response_content_type"] == "application/octet-stream"
        assert "attachment" in call_kwargs["response_content_disposition"]


class TestAttachmentScanResult:
    @patch("contexts.conversation.application.attachment_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.now", return_value=_NOW)
    async def test_quarantined_audits(self, _now, _audit) -> None:
        repo = AsyncMock()
        svc = _make_attachment_service(repo=repo)

        await svc.record_scan_result(
            attachment_id=uuid.uuid4(),
            scan_status=ScanStatus.QUARANTINED,
            actor_ip=None,
        )

        repo.mark_scan.assert_awaited_once()
        _audit.assert_awaited_once()
        assert _audit.call_args[0][1].action == "attachment.quarantined"

    @patch("contexts.conversation.application.attachment_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.conversation.application.attachment_service.now", return_value=_NOW)
    async def test_clean_does_not_audit(self, _now, _audit) -> None:
        repo = AsyncMock()
        svc = _make_attachment_service(repo=repo)

        await svc.record_scan_result(
            attachment_id=uuid.uuid4(),
            scan_status=ScanStatus.CLEAN,
            actor_ip=None,
        )

        repo.mark_scan.assert_awaited_once()
        _audit.assert_not_awaited()


class TestAttachmentBind:
    async def test_bind_delegates(self) -> None:
        repo = AsyncMock()
        repo.bind_to_message.return_value = 3
        svc = _make_attachment_service(repo=repo)

        count = await svc.bind_to_message(
            attachment_ids=[uuid.uuid4()],
            message_id=_MSG,
            chatroom_id=_ROOM,
            uploader_user_id=_USER,
        )

        assert count == 3


# ===========================================================================
# export_service (module-level functions — Redis state machine)
# ===========================================================================


class TestExportService:
    @patch("contexts.conversation.application.export_service.now", return_value=_NOW)
    @patch("contexts.conversation.application.export_service.get_redis")
    async def test_create_and_get(self, mock_redis_fn, _now) -> None:
        from contexts.conversation.application.export_service import create, get

        mock_redis = AsyncMock()
        mock_redis_fn.return_value = mock_redis
        store: dict[str, str] = {}

        async def fake_set(key, val, ex=None):
            store[key] = val

        async def fake_get(key):
            return store.get(key)

        mock_redis.set = fake_set
        mock_redis.get = fake_get

        state = await create(chatroom_id=_ROOM, owner_user_id=_USER)
        assert state.status == "queued"
        assert state.chatroom_id == _ROOM

        retrieved = await get(state.job_id)
        assert retrieved is not None
        assert retrieved.job_id == state.job_id
        assert retrieved.status == "queued"

    @patch("contexts.conversation.application.export_service.now", return_value=_NOW)
    @patch("contexts.conversation.application.export_service.get_redis")
    async def test_state_transitions(self, mock_redis_fn, _now) -> None:
        from contexts.conversation.application.export_service import (
            create,
            get,
            mark_ready,
            mark_running,
        )

        mock_redis = AsyncMock()
        mock_redis_fn.return_value = mock_redis
        store: dict[str, str] = {}

        async def fake_set(key, val, ex=None):
            store[key] = val

        async def fake_get(key):
            return store.get(key)

        mock_redis.set = fake_set
        mock_redis.get = fake_get

        state = await create(chatroom_id=_ROOM, owner_user_id=_USER)

        await mark_running(state.job_id)
        s = await get(state.job_id)
        assert s.status == "running"

        await mark_ready(job_id=state.job_id, bucket="exports", object_key="manifest.json")
        s = await get(state.job_id)
        assert s.status == "ready"
        assert s.object_key == "manifest.json"

    @patch("contexts.conversation.application.export_service.now", return_value=_NOW)
    @patch("contexts.conversation.application.export_service.get_redis")
    async def test_mark_failed(self, mock_redis_fn, _now) -> None:
        from contexts.conversation.application.export_service import (
            create,
            get,
            mark_failed,
        )

        mock_redis = AsyncMock()
        mock_redis_fn.return_value = mock_redis
        store: dict[str, str] = {}

        async def fake_set(key, val, ex=None):
            store[key] = val

        async def fake_get(key):
            return store.get(key)

        mock_redis.set = fake_set
        mock_redis.get = fake_get

        state = await create(chatroom_id=_ROOM, owner_user_id=_USER)
        await mark_failed(job_id=state.job_id, error="boom")
        s = await get(state.job_id)
        assert s.status == "failed"
        assert s.error == "boom"
