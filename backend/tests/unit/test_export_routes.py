"""`POST /api/chatrooms/{id}/export` + `GET /api/exports/{job_id}` delivery.

Dossier: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.5 (chat export).
The link is minted on every GET, so a client that re-fetches status at click
time always gets a live one (Q-4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import exports as routes
from contexts.conversation.application.export_service import ExportJobState, ExportJobStatus
from contexts.conversation.domain.models import ExportSenderScope
from shared_kernel.auth.permissions import Principal
from tests.unit.file_ticket_support import decode

pytestmark = pytest.mark.usefixtures("ticket_signer")

_OWNER = uuid.uuid4()
_ROOM = uuid.uuid4()
_JOB = uuid.uuid4()


def _state(fmt: str, filename: str, *, room_name: str | None = "週三 讀書會") -> ExportJobState:
    return ExportJobState(
        job_id=_JOB,
        chatroom_id=_ROOM,
        owner_user_id=_OWNER,
        status=ExportJobStatus.READY,
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        object_key=f"{_JOB}/{filename}",
        bucket="exports",
        export_format=fmt,
        sender_scope=ExportSenderScope.ALL,
        room_name=room_name,
    )


def _principal() -> Principal:
    return Principal(user_id=_OWNER, is_admin=False, email_verified=True)


@pytest.mark.parametrize(
    ("fmt", "filename", "content_type", "ext"),
    [
        ("markdown", "export.md", "text/markdown; charset=utf-8", "md"),
        ("json", "manifest.json", "application/json", "json"),
        ("pdf", "export.pdf", "application/pdf", "pdf"),
    ],
)
async def test_ready_export_gets_a_browser_link_named_after_the_room(
    fmt: str, filename: str, content_type: str, ext: str
) -> None:
    with patch.object(routes.export_service, "get", AsyncMock(return_value=_state(fmt, filename))):
        out = await routes.get_export(job_id=_JOB, principal=_principal())
    ticket = await decode(out.url)
    assert (ticket.bucket, ticket.key) == ("exports", f"{_JOB}/{filename}")
    assert ticket.content_type == content_type
    assert ticket.disposition.startswith(f'attachment; filename="download-20261011.{ext}"')
    assert f"%E8%AE%80%E6%9B%B8%E6%9C%83-20261011.{ext}" in ticket.disposition


async def test_record_without_room_name_falls_back_to_generic_name() -> None:
    state = _state("markdown", "export.md", room_name=None)
    with patch.object(routes.export_service, "get", AsyncMock(return_value=state)):
        out = await routes.get_export(job_id=_JOB, principal=_principal())
    assert (await decode(out.url)).disposition.startswith('attachment; filename="chat-export-20261011.md"')


async def test_each_get_mints_a_fresh_link() -> None:
    state = _state("markdown", "export.md")
    with patch.object(routes.export_service, "get", AsyncMock(return_value=state)):
        first = await routes.get_export(job_id=_JOB, principal=_principal())
        second = await routes.get_export(job_id=_JOB, principal=_principal())
    assert first.url
    assert second.url
    assert (await decode(first.url)).key == (await decode(second.url)).key


async def test_create_records_the_room_name() -> None:
    access = SimpleNamespace(chatroom=SimpleNamespace(name="週三 讀書會"))
    created = _state("markdown", "export.md")
    create = AsyncMock(return_value=created)
    with (
        patch.object(routes, "resolve_room_access", AsyncMock(return_value=access)),
        patch.object(routes, "ensure_can_read"),
        patch.object(routes, "export_sender_scope", return_value=ExportSenderScope.ALL),
        patch.object(routes.export_service, "create", create),
        patch("shared_kernel.queue.enqueue", AsyncMock()),
    ):
        await routes.create_export(
            chatroom_id=_ROOM,
            body=None,
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
            principal=_principal(),
            db=SimpleNamespace(),  # type: ignore[arg-type]
        )
    assert create.call_args.kwargs["room_name"] == "週三 讀書會"


async def test_state_round_trips_room_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from contexts.conversation.application import export_service

    store: dict[str, str] = {}

    class _Redis:
        async def set(self, key: str, value: str, ex: int) -> None:
            store[key] = value

        async def get(self, key: str) -> str | None:
            return store.get(key)

    monkeypatch.setattr(export_service, "get_redis", _Redis)
    state = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER, room_name="週三 讀書會")
    await export_service.mark_running(state.job_id)
    got = await export_service.get(state.job_id)
    assert got is not None
    assert got.room_name == "週三 讀書會"
