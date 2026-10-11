"""Unit tests for ChatExportService.

Covers: ACL check (admin pass, non-admin with access, non-admin denied),
message serialization (messages + edits + attachments), manifest schema
version, no message cap, MinIO upload with timeout, upload timeout error.
Archive contents (attachment files, omissions) are in test_chat_export_archive.py.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application import chat_export_service as export_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.chat_export_service import ChatExportService
from contexts.conversation.domain.models import (
    AttachmentStatus,
    ExportSenderScope,
    Message,
    MessageAttachment,
    MessageEdit,
    ScanStatus,
    SenderType,
)
from shared_kernel.auth.permissions import Role
from tests.unit.chat_export_fakes import FakeDb, FakeMessages, FakeStore, wire

_NOW = datetime(2026, 6, 23, 12, 0, 0)
_USER = uuid.uuid4()
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()
_MSG = uuid.uuid4()


@pytest.fixture(autouse=True)
def _no_sender_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name resolution has its own suite (test_chat_export_names.py)."""

    async def _none(self: object, chatroom_id: uuid.UUID, rows: object) -> dict[uuid.UUID, str]:
        return {}

    monkeypatch.setattr(ChatExportService, "_sender_names", _none)


def _moderator_access() -> RoomAccess:
    """The unnarrowed path these tests have always exercised.

    Row 19 reads `roles`, so the moderator assumption these cases rely on is
    stated rather than implied. See test_export_authz.py for the narrowed
    (member) and denied (guest) paths.
    """
    room = MagicMock()
    room.name = "general"
    return RoomAccess(
        chatroom=room,
        project_id=uuid.uuid4(),
        roles=frozenset({Role.PROJECT_OWNER}),
        is_guest=False,
    )


def _message(*, msg_id: uuid.UUID | None = None, content: str = "hello") -> Message:
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
        expires_at=None,
    )


class _Env:
    """Wires the service module to fakes; access is a moderator unless changed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.is_admin = False
        self.access = _moderator_access()
        self.ensure = MagicMock()
        self.store = FakeStore()
        self.db = FakeDb()
        self.messages = FakeMessages([])

        async def _resolve(db: object, **_: object) -> RoomAccess:
            return self.access

        monkeypatch.setattr(export_mod, "resolve_room_access", _resolve)
        monkeypatch.setattr(export_mod, "ensure_can_read", self.ensure)
        monkeypatch.setattr(
            export_mod,
            "IdentityFacade",
            MagicMock(side_effect=lambda db: AsyncMock(is_admin=AsyncMock(return_value=self.is_admin))),
        )
        monkeypatch.setattr("shared_kernel.audit.emit", AsyncMock())

    async def export(
        self,
        *,
        messages: list[Message] | None = None,
        edits: dict[uuid.UUID, list[MessageEdit]] | None = None,
        attachments: dict[uuid.UUID, list[MessageAttachment]] | None = None,
        room_name: str = "general",
        **kwargs: Any,
    ) -> tuple[str, str]:
        self.messages = FakeMessages(messages or [])
        wire(
            self.monkeypatch,
            export_mod,
            messages=self.messages,
            store=self.store,
            db=self.db,
            room_name=room_name,
            edits=edits,
            attachments=attachments,
        )
        kwargs.setdefault("recorded_sender_scope", ExportSenderScope.ALL)
        return await ChatExportService(self.db).build_and_upload_export(  # type: ignore[arg-type]
            job_id=_JOB, chatroom_id=_ROOM, owner_user_id=_USER, **kwargs
        )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> _Env:
    return _Env(monkeypatch)


class TestChatExportBuildAndUpload:
    async def test_builds_manifest_with_messages(self, env: _Env) -> None:
        msg = _message()
        bucket, key = await env.export(
            messages=[msg],
            edits={msg.id: [_edit()]},
            attachments={msg.id: [_attachment()]},
            exported_at=_NOW.isoformat(),
            export_format="json",
        )

        assert bucket == "exports"
        assert "manifest.json" in key

        payload = env.store.manifest()
        assert payload["schema_version"] == 2
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

    async def test_admin_can_export(self, env: _Env) -> None:
        env.is_admin = True
        bucket, _key = await env.export(room_name="admin-room")

        assert bucket == "exports"
        env.ensure.assert_called_once()
        assert env.ensure.call_args[1]["is_admin"] is True

    async def test_acl_denied_raises(self, env: _Env) -> None:
        env.ensure.side_effect = PermissionError("no read access")
        with pytest.raises(PermissionError):
            await env.export()
        assert env.store.uploads == {}

    async def test_empty_export(self, env: _Env) -> None:
        await env.export(room_name="empty-room", export_format="json")

        payload = env.store.manifest()
        assert payload["messages"] == []
        assert payload["chatroom"]["name"] == "empty-room"

    async def test_no_message_cap(self, env: _Env) -> None:
        """F-6: the newest messages were dropped past 50,000. The export now
        pages through every row; the repository is asked for no limit."""
        await env.export(messages=[_message(msg_id=uuid.uuid4(), content=f"m{i}") for i in range(7)])

        call = env.messages.calls[0]
        assert "limit" not in call
        # The `✓` cells stay room-wide: the additive sender predicate must not
        # change the moderator path's default.
        assert call["own_user_id"] is None
        assert env.store.text().count("## ") == 7

    async def test_markdown_format(self, env: _Env) -> None:
        _bucket, key = await env.export(messages=[_message(content="hello world")], export_format="markdown")

        assert key.endswith("export.md")
        _, _, content_type = env.store.only_upload()
        assert content_type == "text/markdown; charset=utf-8"
        text = env.store.text()
        assert "# general" in text
        assert "hello world" in text

    async def test_pdf_format_renders_via_weasyprint(self, env: _Env) -> None:
        # Stub the WeasyPrint render so the test needs no system libraries.
        with patch.object(ChatExportService, "_html_to_pdf", return_value=b"%PDF-1.4 stub") as mock_pdf:
            _bucket, key = await env.export(messages=[_message(content="hello")], export_format="pdf")

        assert key.endswith("export.pdf")
        mock_pdf.assert_called_once()
        # The rendered HTML carries the sanitised message body.
        assert "hello" in mock_pdf.call_args.args[0]
        _, body, content_type = env.store.only_upload()
        assert content_type == "application/pdf"
        assert body == b"%PDF-1.4 stub"

    async def test_pdf_window_that_grew_past_the_cap_fails(self, env: _Env) -> None:
        """The API refuses an oversized PDF up front; messages posted after the
        request was accepted still cannot push the render past the cap."""
        from contexts.conversation.domain.errors import ExportTooLargeForPdf

        env.monkeypatch.setattr(export_mod, "pdf_max_messages", lambda: 3)
        with pytest.raises(ExportTooLargeForPdf):
            await env.export(messages=[_message(msg_id=uuid.uuid4()) for _ in range(4)], export_format="pdf")
        assert env.store.uploads == {}

    async def test_date_range_window_passed_to_repo(self, env: _Env) -> None:
        after = datetime(2026, 6, 1, 0, 0, 0)
        before = datetime(2026, 6, 23, 0, 0, 0)
        await env.export(export_format="json", created_after=after, created_before=before)

        call = env.messages.calls[0]
        assert call["created_after"] == after
        assert call["created_before"] == before
        assert call["own_user_id"] is None

    async def test_work_directory_is_removed(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        made: list[Path] = []
        real = export_mod.tempfile.mkdtemp

        def _mkdtemp(**kwargs: Any) -> str:
            path = real(**kwargs)
            made.append(Path(path))
            return path

        monkeypatch.setattr(export_mod.tempfile, "mkdtemp", _mkdtemp)
        await env.export(messages=[_message()])
        env.store.fail_upload = RuntimeError("minio down")
        with pytest.raises(RuntimeError):
            await env.export(messages=[_message()])
        assert len(made) == 2
        assert not any(p.exists() for p in made)


class TestPdfRender:
    def test_render_fetches_nothing_but_inline_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Message markdown can embed `<img src="http://internal/...">`. A
        default WeasyPrint render requests it from the worker's networks
        (SSRF), and `file:` would read its disk. Verified live against
        WeasyPrint 70 in the backend image; this pins the configuration."""
        import sys
        import types

        seen: dict[str, Any] = {}

        class _Fetcher:
            def __init__(self, **kwargs: Any) -> None:
                seen["fetcher"] = kwargs

        class _HTML:
            def __init__(self, **kwargs: Any) -> None:
                seen["html"] = kwargs

            def write_pdf(self) -> bytes:
                return b"%PDF"

        weasy = types.ModuleType("weasyprint")
        weasy.HTML = _HTML  # type: ignore[attr-defined]
        urls = types.ModuleType("weasyprint.urls")
        urls.URLFetcher = _Fetcher  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "weasyprint", weasy)
        monkeypatch.setitem(sys.modules, "weasyprint.urls", urls)

        assert ChatExportService._html_to_pdf("<img src='http://10.0.0.1/'>") == b"%PDF"
        assert seen["fetcher"] == {"allowed_protocols": {"data"}, "allow_redirects": False}
        assert isinstance(seen["html"]["url_fetcher"], _Fetcher)


class TestUpload:
    async def test_upload_timeout_raises(self, tmp_path: Path) -> None:
        minio = AsyncMock()

        async def slow_put(**kwargs: object) -> None:
            await asyncio.sleep(10)

        minio.put_file = slow_put
        payload = tmp_path / "manifest.json"
        payload.write_bytes(b'{"test": true}')

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
            await ChatExportService._upload(_JOB, payload, "manifest.json", "application/json")
