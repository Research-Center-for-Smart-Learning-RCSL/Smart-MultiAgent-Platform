"""Fakes shared by the chat export unit tests.

The export reads pages of messages inside one transaction, closes it, then
downloads, packages and uploads a file, and writes the audit row in a second
transaction (docs/tasks/2026-10-11-chat-export-archive-content/spec.md §7.2).
These fakes stand in for each of those collaborators.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from contexts.conversation.domain.models import (
    Message,
    MessageAttachment,
    MessageEdit,
    SenderType,
)
from shared_kernel.storage.minio_client import ObjectStat


class FakeDb:
    """An AsyncSession whose `begin()` blocks are counted, so a test can see the
    read and audit phases run in separate transactions."""

    def __init__(self) -> None:
        self.transactions = 0
        self.open = False

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[None]:
        assert not self.open, "nested transaction"
        self.transactions += 1
        self.open = True
        try:
            yield
        finally:
            self.open = False

    def begin(self) -> Any:
        return self._tx()


class FakeStore:
    """MinIO: objects to read back, keyed like `MessageAttachment.minio_path`
    (``bucket/key``), and whatever the export uploads."""

    exports_bucket = "exports"
    chat_uploads_bucket = "chat-uploads"

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.uploads: dict[str, tuple[bytes, str]] = {}
        self.downloads: list[str] = []
        self.fail_upload: Exception | None = None
        self.db: FakeDb | None = None

    async def stat(self, *, bucket: str, key: str) -> ObjectStat | None:
        body = self.objects.get(f"{bucket}/{key}")
        return None if body is None else ObjectStat(size=len(body), etag="e", content_type=None)

    async def download_to_path(self, *, bucket: str, key: str, path: Path) -> None:
        assert self.db is None or not self.db.open, "download inside a DB transaction"
        self.downloads.append(f"{bucket}/{key}")
        path.write_bytes(self.objects[f"{bucket}/{key}"])

    async def put_file(self, *, bucket: str, key: str, file_path: str, content_type: str) -> None:
        assert self.db is None or not self.db.open, "upload inside a DB transaction"
        if self.fail_upload is not None:
            raise self.fail_upload
        self.uploads[key] = (Path(file_path).read_bytes(), content_type)

    # ---- reading back what was uploaded ----------------------------------

    def only_upload(self) -> tuple[str, bytes, str]:
        assert len(self.uploads) == 1, self.uploads.keys()
        key, (body, ctype) = next(iter(self.uploads.items()))
        return key, body, ctype

    def manifest(self) -> dict[str, Any]:
        key, body, _ = self.only_upload()
        if key.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                return json.loads(zf.read("manifest.json"))
        return json.loads(body)

    def text(self) -> str:
        return self.only_upload()[1].decode("utf-8")

    def archive(self) -> zipfile.ZipFile:
        key, body, _ = self.only_upload()
        assert key.endswith(".zip"), key
        return zipfile.ZipFile(io.BytesIO(body))


class FakeMessages:
    """`MessageRepository` over a list, applying row 19's documented predicate
    the way the SQL does, and paging like `iter_for_export`."""

    def __init__(self, messages: Sequence[Message], *, page_size: int = 2) -> None:
        self.all = list(messages)
        self.page_size = page_size
        self.calls: list[dict[str, object]] = []

    def _kept(self, own_user_id: object) -> list[Message]:
        if own_user_id is None:
            return list(self.all)
        kept = {SenderType.AGENT, SenderType.SYSTEM}
        return [m for m in self.all if m.sender_type in kept or m.sender_id == own_user_id]

    async def iter_for_export(
        self, chatroom_id: uuid.UUID, **kwargs: Any
    ) -> AsyncIterator[Sequence[Message]]:
        self.calls.append(kwargs)
        rows = self._kept(kwargs.get("own_user_id"))
        for i in range(0, len(rows), self.page_size):
            yield rows[i : i + self.page_size]

    async def count_for_export(self, chatroom_id: uuid.UUID, **kwargs: Any) -> int:
        return len(self._kept(kwargs.get("own_user_id")))


def repo_class(instance: object) -> MagicMock:
    """A stand-in for a repository class whose constructor returns *instance*."""
    return MagicMock(return_value=instance)


def edits_repo(edits: dict[uuid.UUID, list[MessageEdit]] | None = None) -> AsyncMock:
    by_message = edits or {}

    async def _for(ids: Sequence[uuid.UUID]) -> list[MessageEdit]:
        return [e for mid in ids for e in by_message.get(mid, [])]

    return AsyncMock(list_for_messages=AsyncMock(side_effect=_for))


def attachments_repo(attachments: dict[uuid.UUID, list[MessageAttachment]] | None = None) -> AsyncMock:
    by_message = attachments or {}

    async def _for(ids: Sequence[uuid.UUID]) -> list[MessageAttachment]:
        return [a for mid in ids for a in by_message.get(mid, [])]

    return AsyncMock(list_for_messages=AsyncMock(side_effect=_for))


def wire(
    monkeypatch: Any,
    module: Any,
    *,
    messages: FakeMessages,
    store: FakeStore,
    db: FakeDb,
    room_name: str = "general",
    edits: dict[uuid.UUID, list[MessageEdit]] | None = None,
    attachments: dict[uuid.UUID, list[MessageAttachment]] | None = None,
) -> None:
    """Point the export service module at the fakes (access is wired by the caller)."""
    room = MagicMock()
    room.name = room_name
    store.db = db
    monkeypatch.setattr(module, "ChatroomRepository", repo_class(AsyncMock(get=AsyncMock(return_value=room))))
    monkeypatch.setattr(module, "MessageRepository", repo_class(messages))
    monkeypatch.setattr(module, "MessageEditRepository", repo_class(edits_repo(edits)))
    monkeypatch.setattr(module, "MessageAttachmentRepository", repo_class(attachments_repo(attachments)))
    monkeypatch.setattr(module, "get_minio_client", lambda: store)


__all__ = [
    "FakeDb",
    "FakeMessages",
    "FakeStore",
    "attachments_repo",
    "edits_repo",
    "repo_class",
    "wire",
]
