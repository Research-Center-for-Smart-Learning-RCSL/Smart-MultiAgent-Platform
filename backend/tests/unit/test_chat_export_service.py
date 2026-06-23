"""Unit tests for ChatExportService.

Covers: ACL check (admin pass, non-admin with access, non-admin denied),
message serialization (messages + edits + attachments), manifest schema
version, message cap, MinIO upload with timeout, upload timeout error.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application.chat_export_service import (
    _EXPORT_MAX_MESSAGES,
    ChatExportService,
)
from contexts.conversation.domain.models import (
    AttachmentStatus,
    Message,
    MessageAttachment,
    MessageEdit,
    ScanStatus,
    SenderType,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0)
_USER = uuid.uuid4()
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()
_MSG = uuid.uuid4()


def _message(
    *,
    msg_id: uuid.UUID | None = None,
    content: str = "hello",
) -> Message:
    return Message(
        id=msg_id or _MSG,
        chatroom_id=_ROOM,
        sender_type=SenderType.USER,
        sender_id=_USER,
        content_md=content,
        metadata={"type": "chat"},
        version=1,
        created_at=_NOW,
        edited_at=None,
    )


def _edit(*, msg_id: uuid.UUID | None = None) -> MessageEdit:
    return MessageEdit(
        id=uuid.uuid4(),
        message_id=msg_id or _MSG,
        old_content_md="original",
        edited_by_user_id=_USER,
        edited_at=_NOW,
    )


def _attachment(*, msg_id: uuid.UUID | None = None) -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=msg_id or _MSG,
        filename="file.png",
        mime="image/png",
        size_bytes=1024,
        minio_path="chat-uploads/key/file.png",
        status=AttachmentStatus.ACTIVE,
        scan_status=ScanStatus.CLEAN,
        scan_at=_NOW,
        expires_at=_NOW + timedelta(days=3),
    )


def _make_chat_export_service(
    *,
    is_admin: bool = False,
    can_read: bool = True,
    room_name: str = "general",
    messages: list[Message] | None = None,
    edits: list[MessageEdit] | None = None,
    attachments: list[MessageAttachment] | None = None,
) -> tuple[ChatExportService, dict[str, AsyncMock | MagicMock]]:
    db = AsyncMock()
    svc = ChatExportService(db)

    identity = AsyncMock()
    identity.is_admin.return_value = is_admin

    access = MagicMock()
    access.can_read = can_read

    room = MagicMock()
    room.name = room_name

    rooms_repo = AsyncMock()
    rooms_repo.get.return_value = room

    msgs_repo = AsyncMock()
    msgs_repo.all_for_chatroom.return_value = messages or []

    edits_repo = AsyncMock()
    edits_repo.list_for_message.return_value = edits or []

    atts_repo = AsyncMock()
    atts_repo.list_for_message.return_value = attachments or []

    minio = AsyncMock()
    minio.exports_bucket = "exports"

    patches = {
        "identity": identity,
        "access": access,
        "rooms_repo": rooms_repo,
        "msgs_repo": msgs_repo,
        "edits_repo": edits_repo,
        "atts_repo": atts_repo,
        "minio": minio,
    }
    return svc, patches


class TestChatExportBuildAndUpload:
    @patch("contexts.conversation.application.chat_export_service.get_minio_client")
    @patch("contexts.conversation.application.chat_export_service.ChatroomRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageAttachmentRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageEditRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageRepository")
    @patch("contexts.conversation.application.chat_export_service.ensure_can_read")
    @patch("contexts.conversation.application.chat_export_service.resolve_room_access")
    @patch("contexts.conversation.application.chat_export_service.IdentityFacade")
    async def test_builds_manifest_with_messages(
        self,
        MockIdentity,
        mock_resolve,
        mock_ensure,
        MockMsgs,
        MockEdits,
        MockAtts,
        MockRooms,
        mock_minio_fn,
    ) -> None:
        identity = AsyncMock()
        identity.is_admin.return_value = False
        MockIdentity.return_value = identity

        mock_resolve.return_value = MagicMock()

        room = MagicMock()
        room.name = "general"
        rooms = AsyncMock()
        rooms.get.return_value = room
        MockRooms.return_value = rooms

        msg = _message()
        msgs = AsyncMock()
        msgs.all_for_chatroom.return_value = [msg]
        MockMsgs.return_value = msgs

        edit = _edit()
        edits = AsyncMock()
        edits.list_for_message.return_value = [edit]
        MockEdits.return_value = edits

        att = _attachment()
        atts = AsyncMock()
        atts.list_for_message.return_value = [att]
        MockAtts.return_value = atts

        minio = AsyncMock()
        minio.exports_bucket = "exports"
        mock_minio_fn.return_value = minio

        db = AsyncMock()
        svc = ChatExportService(db)

        bucket, key = await svc.build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_USER,
            exported_at=_NOW.isoformat(),
        )

        assert bucket == "exports"
        assert "manifest.json" in key

        put_call = minio.put_object.call_args
        payload = json.loads(put_call.kwargs["data"])
        assert payload["schema_version"] == 1
        assert payload["chatroom"]["name"] == "general"
        assert len(payload["messages"]) == 1

        m = payload["messages"][0]
        assert m["id"] == str(msg.id)
        assert m["content_md"] == "hello"
        assert m["content_html"]
        assert len(m["edits"]) == 1
        assert m["edits"][0]["old_content_md"] == "original"
        assert len(m["attachments"]) == 1
        assert m["attachments"][0]["filename"] == "file.png"

    @patch("contexts.conversation.application.chat_export_service.get_minio_client")
    @patch("contexts.conversation.application.chat_export_service.ChatroomRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageAttachmentRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageEditRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageRepository")
    @patch("contexts.conversation.application.chat_export_service.ensure_can_read")
    @patch("contexts.conversation.application.chat_export_service.resolve_room_access")
    @patch("contexts.conversation.application.chat_export_service.IdentityFacade")
    async def test_admin_can_export(
        self,
        MockIdentity,
        mock_resolve,
        mock_ensure,
        MockMsgs,
        MockEdits,
        MockAtts,
        MockRooms,
        mock_minio_fn,
    ) -> None:
        identity = AsyncMock()
        identity.is_admin.return_value = True
        MockIdentity.return_value = identity

        mock_resolve.return_value = MagicMock()

        room = MagicMock()
        room.name = "admin-room"
        MockRooms.return_value = AsyncMock(get=AsyncMock(return_value=room))
        MockMsgs.return_value = AsyncMock(all_for_chatroom=AsyncMock(return_value=[]))
        MockEdits.return_value = AsyncMock()
        MockAtts.return_value = AsyncMock()

        minio = AsyncMock()
        minio.exports_bucket = "exports"
        mock_minio_fn.return_value = minio

        db = AsyncMock()
        svc = ChatExportService(db)

        bucket, key = await svc.build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_USER,
        )

        assert bucket == "exports"
        mock_ensure.assert_called_once()
        ensure_kwargs = mock_ensure.call_args
        assert ensure_kwargs[1]["is_admin"] is True

    @patch("contexts.conversation.application.chat_export_service.ensure_can_read")
    @patch("contexts.conversation.application.chat_export_service.resolve_room_access")
    @patch("contexts.conversation.application.chat_export_service.IdentityFacade")
    async def test_acl_denied_raises(self, MockIdentity, mock_resolve, mock_ensure) -> None:
        identity = AsyncMock()
        identity.is_admin.return_value = False
        MockIdentity.return_value = identity

        mock_resolve.return_value = MagicMock()
        mock_ensure.side_effect = PermissionError("no read access")

        db = AsyncMock()
        svc = ChatExportService(db)

        with pytest.raises(PermissionError):
            await svc.build_and_upload_export(
                job_id=_JOB,
                chatroom_id=_ROOM,
                owner_user_id=_USER,
            )

    @patch("contexts.conversation.application.chat_export_service.get_minio_client")
    @patch("contexts.conversation.application.chat_export_service.ChatroomRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageAttachmentRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageEditRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageRepository")
    @patch("contexts.conversation.application.chat_export_service.ensure_can_read")
    @patch("contexts.conversation.application.chat_export_service.resolve_room_access")
    @patch("contexts.conversation.application.chat_export_service.IdentityFacade")
    async def test_empty_export(
        self,
        MockIdentity,
        mock_resolve,
        mock_ensure,
        MockMsgs,
        MockEdits,
        MockAtts,
        MockRooms,
        mock_minio_fn,
    ) -> None:
        identity = AsyncMock()
        identity.is_admin.return_value = False
        MockIdentity.return_value = identity

        mock_resolve.return_value = MagicMock()

        room = MagicMock()
        room.name = "empty-room"
        MockRooms.return_value = AsyncMock(get=AsyncMock(return_value=room))
        MockMsgs.return_value = AsyncMock(all_for_chatroom=AsyncMock(return_value=[]))
        MockEdits.return_value = AsyncMock()
        MockAtts.return_value = AsyncMock()

        minio = AsyncMock()
        minio.exports_bucket = "exports"
        mock_minio_fn.return_value = minio

        db = AsyncMock()
        svc = ChatExportService(db)

        bucket, key = await svc.build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_USER,
        )

        put_call = minio.put_object.call_args
        payload = json.loads(put_call.kwargs["data"])
        assert payload["messages"] == []
        assert payload["chatroom"]["name"] == "empty-room"

    @patch("contexts.conversation.application.chat_export_service.get_minio_client")
    @patch("contexts.conversation.application.chat_export_service.ChatroomRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageAttachmentRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageEditRepository")
    @patch("contexts.conversation.application.chat_export_service.MessageRepository")
    @patch("contexts.conversation.application.chat_export_service.ensure_can_read")
    @patch("contexts.conversation.application.chat_export_service.resolve_room_access")
    @patch("contexts.conversation.application.chat_export_service.IdentityFacade")
    async def test_message_cap_applied(
        self,
        MockIdentity,
        mock_resolve,
        mock_ensure,
        MockMsgs,
        MockEdits,
        MockAtts,
        MockRooms,
        mock_minio_fn,
    ) -> None:
        identity = AsyncMock()
        identity.is_admin.return_value = False
        MockIdentity.return_value = identity
        mock_resolve.return_value = MagicMock()

        room = MagicMock()
        room.name = "big-room"
        MockRooms.return_value = AsyncMock(get=AsyncMock(return_value=room))

        msgs_repo = AsyncMock()
        msgs_repo.all_for_chatroom.return_value = []
        MockMsgs.return_value = msgs_repo
        MockEdits.return_value = AsyncMock()
        MockAtts.return_value = AsyncMock()

        minio = AsyncMock()
        minio.exports_bucket = "exports"
        mock_minio_fn.return_value = minio

        db = AsyncMock()
        svc = ChatExportService(db)

        await svc.build_and_upload_export(
            job_id=_JOB,
            chatroom_id=_ROOM,
            owner_user_id=_USER,
        )

        call_kwargs = msgs_repo.all_for_chatroom.call_args
        assert call_kwargs[1]["limit"] == _EXPORT_MAX_MESSAGES


class TestUploadManifest:
    async def test_upload_timeout_raises(self) -> None:
        minio = AsyncMock()

        async def slow_put(**kwargs):
            await asyncio.sleep(10)

        minio.put_object = slow_put

        with (
            patch(
                "contexts.conversation.application.chat_export_service.get_minio_client",
                return_value=minio,
            ),
            patch(
                "contexts.conversation.application.chat_export_service._EXPORT_PUT_TIMEOUT_SECONDS",
                0.01,
            ),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            await ChatExportService._upload_manifest(_JOB, b'{"test": true}')
