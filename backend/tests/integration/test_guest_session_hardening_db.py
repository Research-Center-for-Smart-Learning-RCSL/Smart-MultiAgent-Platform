"""Guest session backend hardening -- AC-1, AC-2, AC-6 at the db tier.

Spec: ``docs/tasks/2026-10-05-guest-session-backend-hardening/spec.md`` §8.1.

WHY THESE ARE DB-TIER TESTS
---------------------------
Every property here is a property of real Postgres: an advisory lock that
blocks a second transaction, a row lock whose waiter re-checks its predicate
after the holder commits, and a JSONB containment query over the audit trail.
The guest service's unit tests mock the repositories and so cannot see any of
them.

The races are made deterministic rather than left to scheduling: the first
transaction runs to completion without committing, the second starts and is
observed either blocked on a lock or finished, and only then does the first
commit. Against the unfixed code the second never blocks and both succeed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import activities as activities_route
from app.api.v1 import attachments as attachments_route
from app.api.v1 import guests as guests_route
from app.api.v1 import messages as messages_route
from contexts.activities.infrastructure import tables as at
from contexts.conversation.application import attachment_service as attachment_service_mod
from contexts.conversation.application import guest_session_service as guest_service_mod
from contexts.conversation.application.guest_session_service import GuestSessionService
from contexts.conversation.domain.errors import GuestCapReached, GuestTokenInvalid
from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.interfaces import error_mapping
from shared_kernel.audit import audit_logs
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

pytestmark = pytest.mark.db

_GUEST_TOKEN = "guest-hardening-itest-token-0123456789"


@dataclass(frozen=True)
class Room:
    project_id: uuid.UUID
    owner_user_id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    activation_id: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    """A guest-link room with a live activity round and no guests yet.

    Rows ride the project cascade. Audit rows name the room in their metadata or
    as their resource, and carry guest or throwaway admin ids the ``project``
    fixture's owner-keyed cleanup does not reach, so they are removed here under
    the retention role.
    """
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="guest-hardening")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="guest-hardening",
                guest_token=_GUEST_TOKEN,
                created_by_user_id=owner_id,
                allow_guest_links=True,
            )
        )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="guest-hardening-type",
                name="guest hardening type",
                validator_kind="in_process",
                validator_config={"validator_id": "filled_count", "min_filled": 1},
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                id=activation_id,
                chatroom_id=chatroom_id,
                activity_type_id=type_id,
                started_by_user_id=owner_id,
            )
        )
        await session.commit()
    try:
        yield Room(project_id, owner_id, chatroom_id, type_id, activation_id)
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("SET ROLE smap_audit_retention"))
            try:
                await cleanup.execute(
                    text(
                        "DELETE FROM audit_logs WHERE resource_id = :cid "
                        "OR metadata->>'chatroom_id' = :cid_text"
                    ),
                    {"cid": chatroom_id, "cid_text": str(chatroom_id)},
                )
            finally:
                await cleanup.execute(text("RESET ROLE"))
            await cleanup.commit()


@pytest.fixture(autouse=True)
def _unsigned_guest_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token signing is not what these tests are about, and the db tier's Vault
    carries no signing key guarantee."""
    monkeypatch.setattr(guest_service_mod, "sign_guest_token", lambda **_: ("guest-jwt", None))


def _cap(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    settings = SimpleNamespace(limits=SimpleNamespace(max_guests_per_chatroom=cap))
    monkeypatch.setattr(guest_service_mod, "get_settings", lambda: settings)


async def _settle_second(
    sessionmaker: async_sessionmaker[AsyncSession], second: asyncio.Task[Any], *, timeout: float = 10.0
) -> None:
    """Return once ``second`` is waiting on a lock or has finished.

    Fixed code blocks the second transaction on the first's lock; unfixed code
    lets it run straight through. Either way the first may now commit, and the
    outcome is decided by the code under test rather than by scheduling.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if second.done():
            return
        async with sessionmaker() as probe:
            waiting = (
                await probe.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                    )
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the second transaction neither blocked nor finished")


async def _race(
    sessionmaker: async_sessionmaker[AsyncSession],
    first: Callable[[AsyncSession], Awaitable[Any]],
    second: Callable[[AsyncSession], Awaitable[Any]],
) -> tuple[Any, Any]:
    """Run ``first`` to completion uncommitted, start ``second``, then commit.

    Each outcome is the call's return value or the exception it raised.
    """

    async def run_second() -> Any:
        async with sessionmaker() as session:
            try:
                result = await second(session)
            except Exception as exc:  # the outcome under test
                await session.rollback()
                return exc
            await session.commit()
            return result

    async with sessionmaker() as session_a:
        result_a: Any = await first(session_a)
        task = asyncio.create_task(run_second())
        try:
            await _settle_second(sessionmaker, task)
        finally:
            await session_a.commit()
        result_b = await asyncio.wait_for(task, timeout=10)
    return result_a, result_b


async def _session_ids(sessionmaker: async_sessionmaker[AsyncSession], room: Room) -> list[uuid.UUID]:
    async with sessionmaker() as read:
        return list(
            (
                await read.execute(
                    sa.select(ct.guest_sessions.c.id).where(
                        ct.guest_sessions.c.chatroom_id == room.chatroom_id
                    )
                )
            )
            .scalars()
            .all()
        )


def _join(room: Room, name: str, browser_id: str | None = None) -> Callable[[AsyncSession], Awaitable[Any]]:
    async def call(session: AsyncSession) -> Any:
        return await GuestSessionService(session).create_or_resume(
            chatroom_id=room.chatroom_id,
            guest_token=_GUEST_TOKEN,
            display_name=name,
            browser_id=browser_id,
        )

    return call


class TestJoinCap:
    """AC-2: [R13.06a] under concurrent joins (F-15)."""

    async def test_concurrent_joins_in_an_empty_room_never_exceed_the_cap(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _cap(monkeypatch, 1)
        first, second = await _race(sessionmaker, _join(room, "Ann"), _join(room, "Ben"))

        assert first.is_resuming is False
        assert isinstance(second, GuestCapReached)
        assert len(await _session_ids(sessionmaker, room)) == 1

    async def test_concurrent_first_joins_from_one_browser_create_one_session(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _cap(monkeypatch, 50)
        first, second = await _race(
            sessionmaker, _join(room, "Ann", browser_id="br-1"), _join(room, "Ann", browser_id="br-1")
        )

        assert not isinstance(second, Exception), second
        assert second.is_resuming is True
        assert second.guest_session_id == first.guest_session_id
        assert len(await _session_ids(sessionmaker, room)) == 1


class TestRefreshRotation:
    """AC-6: [R13.06b] single-use rotation (F-21)."""

    async def test_two_concurrent_refreshes_with_one_cookie_yield_one_success(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            joined = await _join(room, "Ann")(session)
            await session.commit()

        def refresh(session: AsyncSession) -> Awaitable[Any]:
            return GuestSessionService(session).refresh(
                chatroom_id=room.chatroom_id, refresh_token=joined.refresh_token
            )

        first, second = await _race(sessionmaker, refresh, refresh)

        assert first.guest_session_id == joined.guest_session_id
        assert isinstance(second, GuestTokenInvalid)

    async def test_a_cookie_of_another_rooms_session_rotates_nothing(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """The rotation's room predicate is the only thing refusing a cookie
        replayed against another room's path."""
        async with sessionmaker() as session:
            joined = await _join(room, "Ann")(session)
            await session.commit()

        async with sessionmaker() as session:
            with pytest.raises(GuestTokenInvalid):
                await GuestSessionService(session).refresh(
                    chatroom_id=uuid.uuid4(), refresh_token=joined.refresh_token
                )
            await session.rollback()
        async with sessionmaker() as session:
            refreshed = await GuestSessionService(session).refresh(
                chatroom_id=room.chatroom_id, refresh_token=joined.refresh_token
            )
            await session.commit()
        assert refreshed.guest_session_id == joined.guest_session_id

    async def test_a_refusal_after_the_rotation_leaves_the_cookie_valid(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """Q-6: the links-off check follows the rotation, so its refusal must roll
        the rotation back -- the route's transaction is what does it."""
        async with sessionmaker() as session:
            joined = await _join(room, "Ann")(session)
            await session.execute(
                ct.chatrooms.update()
                .where(ct.chatrooms.c.id == room.chatroom_id)
                .values(allow_guest_links=False)
            )
            await session.commit()

        cookie = {f"smap_guest_refresh_{room.chatroom_id}": joined.refresh_token}
        async with _client(_app(sessionmaker, None)) as client:
            refused = await client.post(f"/api/guest/{room.chatroom_id}/refresh", cookies=cookie)
        assert refused.status_code == 403, refused.text

        async with sessionmaker() as session:
            await session.execute(
                ct.chatrooms.update()
                .where(ct.chatrooms.c.id == room.chatroom_id)
                .values(allow_guest_links=True)
            )
            await session.commit()
        async with sessionmaker() as session:
            refreshed = await GuestSessionService(session).refresh(
                chatroom_id=room.chatroom_id, refresh_token=joined.refresh_token
            )
            await session.commit()
        assert refreshed.guest_session_id == joined.guest_session_id


# -- AC-1: one audit shape for every guest-caused row (F-14) --


_ACTOR_IP = "203.0.113.7"


def _app(
    sessionmaker: async_sessionmaker[AsyncSession],
    principal: Principal | None,
    *,
    request_id: uuid.UUID | None = None,
) -> FastAPI:
    app = FastAPI()
    error_mapping.register(app)
    app.include_router(guests_route.router)
    app.include_router(messages_route.chatroom_router)
    app.include_router(messages_route.message_router)
    app.include_router(attachments_route.chatroom_router)
    app.include_router(activities_route.chatroom_router)

    async def _db() -> AsyncIterator[AsyncSession]:
        # The production dependency's contract: commit on success, roll back on error.
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    # What AuthMiddleware would have stamped on the request.
    ctx = RequestContext(request_id=request_id or uuid.uuid4(), actor_ip=_ACTOR_IP, principal=principal)
    app.dependency_overrides[db_session] = _db
    app.dependency_overrides[current_context] = lambda: ctx
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    # https: the refresh cookie may be Secure, and httpx withholds it over http.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def _guest(guest_id: uuid.UUID, room: Room) -> Principal:
    return Principal(
        user_id=guest_id, is_admin=False, email_verified=False, is_guest=True, chatroom_id=room.chatroom_id
    )


@pytest.fixture
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-commit wake-ups and the storage write are outside what is asserted."""
    for name in (
        "_list_bound_agents_for_dispatch",
        "_dispatch_message_wakeups",
        "_dispatch_graphrag_builds",
        "_dispatch_mention_wakeups",
        "_dispatch_message_workflow_signal",
    ):
        monkeypatch.setattr(messages_route, name, AsyncMock(return_value=None))
    minio = SimpleNamespace(chat_uploads_bucket="chat-uploads", put_object=AsyncMock(), remove=AsyncMock())
    monkeypatch.setattr(attachment_service_mod, "get_minio_client", lambda: minio)
    monkeypatch.setattr(attachment_service_mod, "_enqueue_scan", AsyncMock())
    monkeypatch.setattr(attachment_service_mod, "_enqueue_extraction", AsyncMock())


async def _rows(sessionmaker: async_sessionmaker[AsyncSession], actor: uuid.UUID) -> list[sa.Row[Any]]:
    async with sessionmaker() as read:
        return list(
            (
                await read.execute(
                    sa.select(audit_logs.c.action, audit_logs.c.metadata).where(
                        audit_logs.c.actor_user_id == actor
                    )
                )
            ).all()
        )


@pytest.mark.usefixtures("_quiet_side_effects")
class TestGuestAuditTrail:
    async def test_every_guest_caused_row_carries_the_guest_shape(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        cid = room.chatroom_id
        async with _client(_app(sessionmaker, None)) as public:
            joined = await public.post(
                f"/api/guest/{cid}/{_GUEST_TOKEN}/session", json={"display_name": "Ann", "browser_id": "br-a"}
            )
            assert joined.status_code == 200, joined.text
            guest_id = uuid.UUID(joined.json()["guest_session_id"])
            refreshed = await public.post(f"/api/guest/{cid}/refresh")
            assert refreshed.status_code == 200, refreshed.text

        async with _client(_app(sessionmaker, _guest(guest_id, room))) as client:
            sent = await client.post(f"/api/chatrooms/{cid}/messages", json={"content_md": "hello"})
            assert sent.status_code == 201, sent.text
            uploaded = await client.post(
                f"/api/chatrooms/{cid}/attachments", files={"file": ("notes.txt", b"notes", "text/plain")}
            )
            assert uploaded.status_code == 201, uploaded.text
            renamed = await client.put(
                f"/api/guest/session/{guest_id}/display-name", json={"display_name": "Annie"}
            )
            assert renamed.status_code == 200, renamed.text
            submitted = await client.post(
                f"/api/chatrooms/{cid}/activity-submissions",
                json={"activity_type_id": str(room.activity_type_id), "payload": {"answer": "x"}},
            )
            assert submitted.status_code == 200, submitted.text
            completed = await client.patch(
                f"/api/chatrooms/{cid}/activity-activations/{room.activation_id}/completion",
                json={"completed": True},
            )
            assert completed.status_code == 200, completed.text
            closed = await client.patch(
                f"/api/chatrooms/{cid}/activity-sessions/{completed.json()['id']}/close"
            )
            assert closed.status_code == 200, closed.text
            deleted = await client.delete(f"/api/messages/{sent.json()['id']}")
            assert deleted.status_code == 204, deleted.text

        async with sessionmaker() as read:
            tagged = (
                (
                    await read.execute(
                        sa.select(audit_logs.c.action).where(
                            sa.and_(
                                audit_logs.c.actor_user_id == guest_id,
                                audit_logs.c.metadata.contains({"guest": True, "chatroom_id": str(cid)}),
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(tagged) == sorted(
            [
                "guest.session.created",
                "guest.session.refreshed",
                "guest.session.renamed",
                "message.sent",
                "attachment.uploaded",
                "activity.submitted",
                "activity.session_completed",
                "activity.session_closed",
                "message.deleted",
            ]
        )
        # The session rows keep the key existing investigation queries read.
        for action, metadata in await _rows(sessionmaker, guest_id):
            if action.startswith("guest.session."):
                assert metadata["guest_session_id"] == str(guest_id)

    async def test_a_refresh_row_records_the_request_context(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        cid = room.chatroom_id
        request_id = uuid.uuid4()
        async with _client(_app(sessionmaker, None, request_id=request_id)) as public:
            joined = await public.post(
                f"/api/guest/{cid}/{_GUEST_TOKEN}/session", json={"display_name": "Ann"}
            )
            assert joined.status_code == 200, joined.text
            refreshed = await public.post(f"/api/guest/{cid}/refresh")
            assert refreshed.status_code == 200, refreshed.text

        async with sessionmaker() as read:
            row = (
                await read.execute(
                    sa.select(audit_logs.c.actor_ip, audit_logs.c.request_id).where(
                        sa.and_(
                            audit_logs.c.action == "guest.session.refreshed",
                            audit_logs.c.resource_id == cid,
                        )
                    )
                )
            ).one()
        assert row.request_id == request_id
        assert str(row.actor_ip) == _ACTOR_IP

    async def test_an_admin_acting_on_a_guests_message_is_the_untagged_actor(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        cid = room.chatroom_id
        async with _client(_app(sessionmaker, None)) as public:
            joined = await public.post(
                f"/api/guest/{cid}/{_GUEST_TOKEN}/session", json={"display_name": "Ann"}
            )
            guest_id = uuid.UUID(joined.json()["guest_session_id"])
        async with _client(_app(sessionmaker, _guest(guest_id, room))) as client:
            sent = await client.post(f"/api/chatrooms/{cid}/messages", json={"content_md": "hello"})
            assert sent.status_code == 201, sent.text

        admin = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True)
        async with _client(_app(sessionmaker, admin)) as client:
            deleted = await client.delete(f"/api/messages/{sent.json()['id']}")
            assert deleted.status_code == 204, deleted.text

        rows = await _rows(sessionmaker, admin.user_id)
        assert [action for action, _ in rows] == ["message.deleted"]
        assert "guest" not in rows[0][1]
