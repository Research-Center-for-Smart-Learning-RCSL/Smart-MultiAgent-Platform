"""The chat export archive: attachment files, omissions, and safe entry names.

Dossier: docs/tasks/2026-10-11-chat-export-archive-content/spec.md (F-4, Q-1..Q-3,
§7.7). [R13.17]: "a folder of attachments (attachments are regenerated from MinIO
if still alive; missing ones are noted)".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.conversation.application import chat_export_service as export_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.chat_export_service import ChatExportService
from contexts.conversation.domain.models import (
    AttachmentStatus,
    ExportSenderScope,
    Message,
    MessageAttachment,
    ScanStatus,
    SenderType,
)
from shared_kernel.auth.permissions import Role
from tests.unit.chat_export_fakes import FakeDb, FakeMessages, FakeStore, wire

_NOW = datetime(2026, 10, 11, 12, 0, 0)
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()
_OWNER = uuid.uuid4()
_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
_TXT = "notes, 筆記\n".encode()


def _message(content: str = "hello") -> Message:
    return Message(
        id=uuid.uuid4(),
        chatroom_id=_ROOM,
        sender_type=SenderType.USER,
        sender_id=_OWNER,
        content_md=content,
        metadata={},
        version=1,
        created_at=_NOW,
        edited_at=None,
    )


def _attachment(
    message: Message,
    filename: str,
    *,
    key: str | None = None,
    size: int = 10,
    mime: str = "application/octet-stream",
    status: AttachmentStatus = AttachmentStatus.ACTIVE,
    scan: ScanStatus = ScanStatus.CLEAN,
    expires_at: datetime | None = None,
) -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=message.id,
        filename=filename,
        mime=mime,
        size_bytes=size,
        minio_path=key or f"chat-uploads/proj/{_ROOM}/{uuid.uuid4()}/{filename}",
        status=status,
        scan_status=scan,
        scan_at=_NOW,
        expires_at=expires_at,
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    room = MagicMock()
    room.name = "general"
    access = RoomAccess(
        chatroom=room, project_id=uuid.uuid4(), roles=frozenset({Role.PROJECT_OWNER}), is_guest=False
    )

    async def _resolve(db: object, **_: object) -> RoomAccess:
        return access

    async def _no_names(self: object, chatroom_id: uuid.UUID, rows: object) -> dict[uuid.UUID, str]:
        return {}

    emit = AsyncMock()
    monkeypatch.setattr(export_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(export_mod, "ensure_can_read", MagicMock())
    monkeypatch.setattr(
        export_mod,
        "IdentityFacade",
        MagicMock(return_value=AsyncMock(is_admin=AsyncMock(return_value=False))),
    )
    monkeypatch.setattr(ChatExportService, "_sender_names", _no_names)
    monkeypatch.setattr("shared_kernel.audit.emit", emit)
    return {"monkeypatch": monkeypatch, "emit": emit}


async def _export(
    env: dict[str, Any],
    *,
    messages: list[Message],
    attachments: dict[uuid.UUID, list[MessageAttachment]],
    objects: dict[str, bytes],
    export_format: str = "json",
    include_attachments: bool = True,
    budget: int | None = None,
) -> tuple[FakeStore, FakeDb]:
    store, db = FakeStore(objects), FakeDb()
    wire(
        env["monkeypatch"],
        export_mod,
        messages=FakeMessages(messages),
        store=store,
        db=db,
        attachments=attachments,
    )
    if budget is not None:
        env["monkeypatch"].setattr(export_mod, "_max_attachment_bytes", lambda: budget)
    await ChatExportService(db).build_and_upload_export(  # type: ignore[arg-type]
        job_id=_JOB,
        chatroom_id=_ROOM,
        owner_user_id=_OWNER,
        recorded_sender_scope=ExportSenderScope.ALL,
        export_format=export_format,
        include_attachments=include_attachments,
    )
    return store, db


async def test_json_with_attachments_is_a_zip_of_manifest_and_files(env: dict[str, Any]) -> None:
    m = _message()
    png = _attachment(m, "diagram.png", mime="image/png", size=len(_PNG))
    txt = _attachment(m, "notes.txt", mime="text/plain", size=len(_TXT))
    store, db = await _export(
        env,
        messages=[m],
        attachments={m.id: [png, txt]},
        objects={png.minio_path: _PNG, txt.minio_path: _TXT},
    )

    key, _, content_type = store.only_upload()
    assert key.endswith("export.zip")
    assert content_type == "application/zip"
    zf = store.archive()
    assert zf.read(f"attachments/{m.id}/diagram.png") == _PNG
    assert zf.read(f"attachments/{m.id}/notes.txt") == _TXT

    manifest = store.manifest()
    assert manifest["schema_version"] == 2
    entries = {a["filename"]: a for a in manifest["messages"][0]["attachments"]}
    assert entries["diagram.png"]["archived"] is True
    assert entries["diagram.png"]["archive_path"] == f"attachments/{m.id}/diagram.png"
    assert "omitted_reason" not in entries["diagram.png"]
    # Reads and the audit row each run in their own transaction; the file
    # transfer runs in neither (FakeStore asserts that).
    assert db.transactions == 2


async def test_missing_attachments_are_omitted_with_their_reason(env: dict[str, Any]) -> None:
    m = _message()
    kept = _attachment(m, "kept.bin", size=6)
    expired = _attachment(m, "old.bin", status=AttachmentStatus.EXPIRED)
    # Past its horizon but not yet swept: the row still reads active (R13.11a).
    lapsed = _attachment(m, "lapsed.bin", expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    quarantined = _attachment(
        m, "virus.exe", status=AttachmentStatus.QUARANTINED, scan=ScanStatus.QUARANTINED
    )
    scan_flagged = _attachment(m, "flagged.exe", scan=ScanStatus.QUARANTINED)
    gone = _attachment(m, "gone.bin")
    too_big = _attachment(m, "big.bin", size=10)
    after_budget = _attachment(m, "small.bin", size=1)
    store, _ = await _export(
        env,
        messages=[m],
        attachments={m.id: [kept, expired, lapsed, quarantined, scan_flagged, gone, too_big, after_budget]},
        objects={
            kept.minio_path: b"123456",
            expired.minio_path: b"x",
            lapsed.minio_path: b"x",
            quarantined.minio_path: b"MZ",
            scan_flagged.minio_path: b"MZ",
            too_big.minio_path: b"0123456789",
            after_budget.minio_path: b"1",
        },
        budget=8,
    )

    reasons = {a["filename"]: a.get("omitted_reason") for a in store.manifest()["messages"][0]["attachments"]}
    assert reasons == {
        "kept.bin": None,
        "old.bin": "expired",
        "lapsed.bin": "expired",
        "virus.exe": "quarantined",
        "flagged.exe": "quarantined",
        "gone.bin": "missing",
        "big.bin": "size_budget",
        # Q-3: filled in transcript order until the next file would exceed the
        # budget; everything after is omitted, even a file that would fit.
        "small.bin": "size_budget",
    }
    zf = store.archive()
    assert {n for n in zf.namelist() if n.startswith("attachments/")} == {f"attachments/{m.id}/kept.bin"}
    readme = zf.read("README.txt").decode()
    for name in ("old.bin", "lapsed.bin", "virus.exe", "flagged.exe", "gone.bin", "big.bin", "small.bin"):
        assert name in readme
    assert "kept.bin" not in readme
    # A quarantined object is a scan verdict: never even read.
    assert quarantined.minio_path not in store.downloads
    assert scan_flagged.minio_path not in store.downloads


async def test_no_storage_path_appears_in_any_format(env: dict[str, Any]) -> None:
    m = _message()
    att = _attachment(m, "a.txt", key="chat-uploads/proj/room/secret-layout/a.txt")
    for export_format in ("json", "markdown"):
        for include in (True, False):
            store, _ = await _export(
                env,
                messages=[m],
                attachments={m.id: [att]},
                objects={att.minio_path: b"a"},
                export_format=export_format,
                include_attachments=include,
            )
            _, body, _ = store.only_upload()
            assert b"secret-layout" not in body
            assert b"minio_path" not in body


async def test_markdown_notes_archived_and_omitted_files(env: dict[str, Any]) -> None:
    m = _message("see attached")
    att = _attachment(m, "a.txt")
    old = _attachment(m, "old.txt", status=AttachmentStatus.EXPIRED)
    store, _ = await _export(
        env,
        messages=[m],
        attachments={m.id: [att, old]},
        objects={att.minio_path: b"a"},
        export_format="markdown",
    )
    transcript = store.archive().read("export.md").decode()
    assert f"attachments/{m.id}/a.txt" in transcript
    assert "_old.txt: not included (expired)_" in transcript


@pytest.mark.parametrize(
    ("hostile", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("a/b", "b"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("evil\r\nX-Header: 1.txt", "evilX-Header 1.txt"),
        ("/abs/path.txt", "path.txt"),
        ("..", "file"),
    ],
)
async def test_hostile_filename_yields_one_safe_entry(
    env: dict[str, Any], hostile: str, expected: str
) -> None:
    m = _message()
    att = _attachment(m, hostile)
    store, _ = await _export(env, messages=[m], attachments={m.id: [att]}, objects={att.minio_path: b"z"})
    entries = [n for n in store.archive().namelist() if n.startswith("attachments/")]
    assert entries == [f"attachments/{m.id}/{expected}"]
    for name in store.archive().namelist():
        assert ".." not in name.split("/")
        assert not name.startswith("/")
        assert "\\" not in name


async def test_duplicate_names_in_one_message_do_not_collide(env: dict[str, Any]) -> None:
    m = _message()
    first, second = _attachment(m, "photo.png"), _attachment(m, "photo.png")
    store, _ = await _export(
        env,
        messages=[m],
        attachments={m.id: [first, second]},
        objects={first.minio_path: b"1", second.minio_path: b"2"},
    )
    zf = store.archive()
    paths = [a["archive_path"] for a in store.manifest()["messages"][0]["attachments"]]
    assert len(set(paths)) == 2
    assert [zf.read(p) for p in paths] == [b"1", b"2"]


async def test_without_attachments_the_export_is_the_single_transcript(env: dict[str, Any]) -> None:
    m = _message()
    att = _attachment(m, "a.txt")
    store, _ = await _export(
        env,
        messages=[m],
        attachments={m.id: [att]},
        objects={att.minio_path: b"a"},
        include_attachments=False,
    )
    key, _, content_type = store.only_upload()
    assert key.endswith("manifest.json")
    assert content_type == "application/json"
    assert store.downloads == []
    entry = store.manifest()["messages"][0]["attachments"][0]
    assert entry["filename"] == "a.txt"
    assert entry["archived"] is False


async def test_audit_counts_archived_and_omitted(env: dict[str, Any]) -> None:
    m = _message()
    kept = _attachment(m, "a.txt")
    old = _attachment(m, "b.txt", status=AttachmentStatus.EXPIRED)
    await _export(env, messages=[m], attachments={m.id: [kept, old]}, objects={kept.minio_path: b"a"})
    event = env["emit"].await_args.args[1]
    assert event.metadata["include_attachments"] is True
    assert event.metadata["attachments_archived"] == 1
    assert event.metadata["attachments_omitted"] == 1
    assert "a.txt" not in json.dumps(event.metadata)


async def test_every_page_is_exported_in_order(env: dict[str, Any]) -> None:
    """F-6: no cap. The fake pages two at a time; all five arrive, in order."""
    msgs = [_message(f"m{i}") for i in range(5)]
    store, _ = await _export(env, messages=msgs, attachments={}, objects={}, include_attachments=False)
    assert [x["content_md"] for x in store.manifest()["messages"]] == [f"m{i}" for i in range(5)]
