"""`POST /api/chatrooms/{id}/export` + `GET /api/exports/{job_id}` delivery.

Dossier: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.5 (chat export).
The link is minted on every GET, so a client that re-fetches status at click
time always gets a live one (Q-4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
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


class _Redis:
    """The subset of redis.asyncio the export job store uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    from contexts.conversation.application import export_service

    fake = _Redis()
    monkeypatch.setattr(export_service, "get_redis", lambda: fake)
    return fake


async def test_state_round_trips_room_name(redis: _Redis) -> None:
    from contexts.conversation.application import export_service

    state = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER, room_name="週三 讀書會")
    await export_service.mark_running(state.job_id)
    got = await export_service.get(state.job_id)
    assert got is not None
    assert got.room_name == "週三 讀書會"


async def test_state_round_trips_include_attachments(redis: _Redis) -> None:
    from contexts.conversation.application import export_service

    state = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER, include_attachments=True)
    got = await export_service.get(state.job_id)
    assert got is not None
    assert got.include_attachments is True


# ---- Q-6: one export in progress per user per room --------------------------


async def test_second_export_while_one_is_active_is_refused_with_its_id(redis: _Redis) -> None:
    from contexts.conversation.application import export_service
    from contexts.conversation.domain.errors import ExportInProgress

    first = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
    with pytest.raises(ExportInProgress) as exc:
        await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
    assert exc.value.job_id == first.job_id

    # Another room, or another user in this room, is not blocked.
    await export_service.create(chatroom_id=uuid.uuid4(), owner_user_id=_OWNER)
    await export_service.create(chatroom_id=_ROOM, owner_user_id=uuid.uuid4())


@pytest.mark.parametrize("finish", ["ready", "failed"])
async def test_a_finished_export_frees_the_room(redis: _Redis, finish: str) -> None:
    from contexts.conversation.application import export_service

    first = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
    if finish == "ready":
        await export_service.mark_ready(job_id=first.job_id, bucket="exports", object_key="k")
    else:
        await export_service.mark_failed(job_id=first.job_id, error="boom")
    second = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
    assert second.job_id != first.job_id


async def test_an_abandoned_export_does_not_block_the_room(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker killed mid-job never marks it; past the longest a job can run,
    the room is free again and the stale job reads failed, not running."""
    from contexts.conversation.application import export_service
    from shared_kernel.auth import clients

    start = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)
    clients.set_clock(lambda: start)
    try:
        first = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
        await export_service.mark_running(first.job_id)
        clients.set_clock(lambda: start + export_service.ACTIVE_EXPORT_STALE_AFTER + timedelta(seconds=1))
        second = await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)
    finally:
        clients.set_clock(lambda: datetime.now(UTC))
    assert second.job_id != first.job_id
    stale = await export_service.get(first.job_id)
    assert stale is not None
    assert stale.status == ExportJobStatus.FAILED


async def test_a_failed_enqueue_frees_the_room(redis: _Redis) -> None:
    access = SimpleNamespace(chatroom=SimpleNamespace(name="r"))
    with (
        patch.object(routes, "resolve_room_access", AsyncMock(return_value=access)),
        patch.object(routes, "ensure_can_read"),
        patch.object(routes, "export_sender_scope", return_value=ExportSenderScope.ALL),
        patch("shared_kernel.queue.enqueue", AsyncMock(side_effect=ConnectionError("redis queue down"))),
        pytest.raises(ConnectionError),
    ):
        await routes.create_export(
            chatroom_id=_ROOM,
            body=None,
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
            principal=_principal(),
            db=SimpleNamespace(),  # type: ignore[arg-type]
        )
    from contexts.conversation.application import export_service

    await export_service.create(chatroom_id=_ROOM, owner_user_id=_OWNER)  # not 409


# ---- Q-4: PDF is refused up front above its cap --------------------------------


def _create_patches(count: int, *, scope: ExportSenderScope = ExportSenderScope.ALL) -> Any:
    access = SimpleNamespace(chatroom=SimpleNamespace(name="r"))
    counter = AsyncMock(return_value=count)
    return counter, (
        patch.object(routes, "resolve_room_access", AsyncMock(return_value=access)),
        patch.object(routes, "ensure_can_read"),
        patch.object(routes, "export_sender_scope", return_value=scope),
        patch("contexts.conversation.application.chat_export_service.count_export_messages", counter),
        patch("shared_kernel.queue.enqueue", AsyncMock()),
    )


async def _create(body: Any) -> Any:
    return await routes.create_export(
        chatroom_id=_ROOM,
        body=body,
        ctx=SimpleNamespace(),  # type: ignore[arg-type]
        principal=_principal(),
        db=SimpleNamespace(),  # type: ignore[arg-type]
    )


async def test_pdf_over_the_cap_is_refused_before_any_job_exists(redis: _Redis) -> None:
    from contexts.conversation.domain.errors import ExportTooLargeForPdf

    counter, patches = _create_patches(5001, scope=ExportSenderScope.OWN_PLUS_NON_USER)
    with patches[0], patches[1], patches[2], patches[3], patches[4] as enqueue:
        with pytest.raises(ExportTooLargeForPdf) as exc:
            await _create(routes.ExportCreateIn(format="pdf"))
        enqueue.assert_not_awaited()
    assert (exc.value.message_count, exc.value.limit) == (5001, 5000)
    assert redis.store == {}
    # Counted under the caller's own row-19 scope, not the whole room.
    assert counter.await_args.kwargs["own_user_id"] == _OWNER


async def test_pdf_at_the_cap_and_text_formats_are_accepted(redis: _Redis) -> None:
    _, patches = _create_patches(5000)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        await _create(routes.ExportCreateIn(format="pdf"))
    redis.store.clear()  # the room's one active slot, held by the job above
    counter_md, patches_md = _create_patches(10**6)
    with patches_md[0], patches_md[1], patches_md[2], patches_md[3], patches_md[4]:
        await _create(routes.ExportCreateIn(format="markdown", include_attachments=False))
    counter_md.assert_not_awaited()  # text formats have no cap, so nothing to count


async def test_include_attachments_defaults_on_and_names_a_zip(redis: _Redis) -> None:
    assert routes.ExportCreateIn().include_attachments is True
    zipped = ExportJobState(
        job_id=_JOB,
        chatroom_id=_ROOM,
        owner_user_id=_OWNER,
        status=ExportJobStatus.READY,
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        object_key=f"{_JOB}/export.zip",
        bucket="exports",
        export_format="json",
        include_attachments=True,
        room_name="room",
    )
    with patch.object(routes.export_service, "get", AsyncMock(return_value=zipped)):
        out = await routes.get_export(job_id=_JOB, principal=_principal())
    ticket = await decode(out.url)
    assert ticket.content_type == "application/zip"
    assert ticket.disposition.startswith('attachment; filename="room-20261011.zip"')


def test_export_errors_render_their_fields() -> None:
    """The client reads the count and the running job id off the problem body."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from contexts.conversation.domain.errors import ExportInProgress, ExportTooLargeForPdf
    from contexts.conversation.interfaces import error_mapping

    app = FastAPI()
    error_mapping.register(app)
    running = uuid.uuid4()

    @app.get("/pdf")
    def _pdf() -> None:
        raise ExportTooLargeForPdf(message_count=7000, limit=5000)

    @app.get("/busy")
    def _busy() -> None:
        raise ExportInProgress(job_id=running)

    client = TestClient(app)
    pdf = client.get("/pdf")
    assert pdf.status_code == 422
    assert pdf.json()["type"].endswith("/conversation/export-too-large-for-pdf")
    assert (pdf.json()["message_count"], pdf.json()["limit"]) == (7000, 5000)
    busy = client.get("/busy")
    assert busy.status_code == 409
    assert busy.json()["type"].endswith("/conversation/export-in-progress")
    assert busy.json()["job_id"] == str(running)
