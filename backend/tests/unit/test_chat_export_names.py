"""Chat exports name each sender, never by email -- name-fallback AC-1, AC-5.

Spec: ``docs/tasks/2026-10-07-name-fallback-surfaces/spec.md`` §7.3, §8.

The manifest carried only ``sender_type`` and ``sender_id``, and the Markdown and
PDF transcripts labelled every message ``<type> (<8 chars of id>)``. The fix
resolves names once per export: agents through ``AgentsFacade.agent_names``,
guests through the room's guest labels, accounts through ``get_display_names``
(never ``get_chat_labels``, which falls back to email).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import contexts.conversation.application.chat_export_service as export_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.chat_export_service import ChatExportService
from contexts.conversation.application.room_guests import RoomGuestLabels
from contexts.conversation.domain.models import ExportSenderScope, Message, SenderType
from shared_kernel.auth.permissions import Role

_NOW = datetime(2026, 10, 7, 12, 0, 0)
_OWNER = uuid.uuid4()
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()

_TEACHER, _NAMELESS, _REGISTERED, _SESSION, _AGENT, _DELETED_AGENT = (uuid.uuid4() for _ in range(6))


def _msg(sender_type: SenderType, sender_id: uuid.UUID | None, content: str) -> Message:
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


_MESSAGES = [
    _msg(SenderType.USER, _TEACHER, "from-teacher"),
    _msg(SenderType.USER, _NAMELESS, "from-nameless"),
    _msg(SenderType.USER, _REGISTERED, "from-registered"),
    _msg(SenderType.GUEST, _SESSION, "from-session"),
    _msg(SenderType.AGENT, _AGENT, "from-agent"),
    _msg(SenderType.AGENT, _DELETED_AGENT, "from-gone-agent"),
    _msg(SenderType.SYSTEM, None, "from-system"),
]


@pytest.fixture
def minio(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    room = MagicMock()
    room.name = "general"
    access = RoomAccess(
        chatroom=room, project_id=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}), is_guest=False
    )

    async def _resolve(db: object, *, principal: object, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    class _Identity:
        def __init__(self, db: object) -> None:
            pass

        async def is_admin(self, user_id: uuid.UUID) -> bool:
            return False

        async def get_display_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
            names: dict[uuid.UUID, str | None] = {
                _TEACHER: "Teacher",
                _NAMELESS: None,
                _REGISTERED: "Account",
            }
            return {i: n for i, n in names.items() if i in ids}

        async def get_chat_labels(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
            return dict.fromkeys(ids, "leak@example.com")

    class _Agents:
        def __init__(self, db: object) -> None:
            pass

        async def agent_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
            return {i: n for i, n in {_AGENT: "Tutor"}.items() if i in ids}

    async def _guests(db: object, chatroom_id: uuid.UUID) -> RoomGuestLabels:
        return RoomGuestLabels(sessions={_SESSION: "Sam"}, registered={_REGISTERED: "Olive"})

    def _repo(**methods: Any) -> MagicMock:
        return MagicMock(return_value=AsyncMock(**methods))

    store = AsyncMock()
    store.exports_bucket = "exports"
    monkeypatch.setattr(export_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(export_mod, "ensure_can_read", MagicMock())
    monkeypatch.setattr(export_mod, "IdentityFacade", _Identity)
    monkeypatch.setattr(export_mod, "AgentsFacade", _Agents)
    monkeypatch.setattr(export_mod, "load_room_guest_labels", _guests)
    monkeypatch.setattr(export_mod, "ChatroomRepository", _repo(get=AsyncMock(return_value=room)))
    monkeypatch.setattr(
        export_mod, "MessageRepository", _repo(all_for_chatroom=AsyncMock(return_value=_MESSAGES))
    )
    monkeypatch.setattr(
        export_mod, "MessageEditRepository", _repo(list_for_message=AsyncMock(return_value=[]))
    )
    monkeypatch.setattr(
        export_mod, "MessageAttachmentRepository", _repo(list_for_message=AsyncMock(return_value=[]))
    )
    monkeypatch.setattr(export_mod, "get_minio_client", lambda: store)
    return store


async def _export(export_format: str) -> None:
    await ChatExportService(AsyncMock()).build_and_upload_export(
        job_id=_JOB,
        chatroom_id=_ROOM,
        owner_user_id=_OWNER,
        recorded_sender_scope=ExportSenderScope.ALL,
        export_format=export_format,
    )


async def test_manifest_carries_each_senders_name(minio: AsyncMock) -> None:
    await _export("json")

    payload = json.loads(minio.put_object.call_args.kwargs["data"])
    names = {m["content_md"]: m["sender_name"] for m in payload["messages"]}
    assert names == {
        "from-teacher": "Teacher",
        "from-nameless": None,
        "from-registered": "Olive",
        "from-session": "Sam",
        "from-agent": "Tutor",
        "from-gone-agent": None,
        "from-system": None,
    }


async def test_markdown_labels_by_name_and_keeps_the_short_id_otherwise(minio: AsyncMock) -> None:
    await _export("markdown")

    text = minio.put_object.call_args.kwargs["data"].decode("utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split(" — ")[0] for h in headings] == [
        "## Teacher",
        f"## user ({str(_NAMELESS)[:8]})",
        "## Olive",
        "## Sam",
        "## Tutor",
        f"## agent ({str(_DELETED_AGENT)[:8]})",
        "## system",
    ]
    assert "@" not in text


async def test_pdf_labels_by_name(minio: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    rendered: list[str] = []

    def _to_pdf(html: str) -> bytes:
        rendered.append(html)
        return b"%PDF stub"

    monkeypatch.setattr(ChatExportService, "_html_to_pdf", staticmethod(_to_pdf))

    await _export("pdf")

    assert '<div class="msg-head">Tutor — ' in rendered[0]
    assert '<div class="msg-head">Teacher — ' in rendered[0]
    assert "@" not in rendered[0]
