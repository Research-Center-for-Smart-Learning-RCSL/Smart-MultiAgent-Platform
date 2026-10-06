"""Removing, banning and rotating against real Postgres -- AC-1..AC-5, AC-7, AC-9.

Spec: ``docs/tasks/2026-10-05-guest-kick-and-ban/spec.md`` §12.

WHY THESE ARE DB-TIER TESTS
---------------------------
The removal is enforced by reading the session row on every guest request, the
ban by a lookup that must survive the session row being purged, and the cap by
a count that must skip revoked rows. Each is a property of the real tables and
queries; the service unit tests mock the repositories and cannot see them.

The guest principal is built as ``AuthMiddleware._handle_guest`` builds it: the
token outlives the removal, so every refusal here comes from the row check.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.api.v1 import attachments as attachments_route
from app.api.v1 import canvas as canvas_route
from app.api.v1 import guests as guests_route
from app.api.v1 import messages as messages_route
from contexts.conversation.application import guest_session_service as guest_service_mod
from contexts.conversation.application.guest_session_service import GuestSessionService
from contexts.conversation.domain.errors import GuestCapReached, GuestRemoved, GuestTokenInvalid
from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.infrastructure.repositories import GuestSessionRepository
from shared_kernel.audit import audit_logs
from shared_kernel.auth.clients import now
from shared_kernel.auth.permissions import Principal
from tests.integration.guest_db_kit import (
    guest_principal,
    https_client,
    purge_room_audit,
    quiet_message_side_effects,
    route_app,
)

pytestmark = pytest.mark.db

_GUEST_TOKEN = "guest-kick-ban-itest-token-0123456789"


@dataclass(frozen=True)
class Room:
    project_id: uuid.UUID
    owner_user_id: uuid.UUID
    chatroom_id: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    """A guest-link room. Rows ride the project cascade; audit rows naming the
    room are removed under the retention role."""
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="guest-kick-ban")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="guest-kick-ban",
                guest_token=_GUEST_TOKEN,
                created_by_user_id=owner_id,
                allow_guest_links=True,
            )
        )
        await session.commit()
    try:
        yield Room(project_id, owner_id, chatroom_id)
    finally:
        await purge_room_audit(sessionmaker, chatroom_id)


@pytest.fixture(autouse=True)
def _unsigned_guest_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guest_service_mod, "sign_guest_token", lambda **_: ("guest-jwt", None))


@pytest.fixture
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet_message_side_effects(monkeypatch)


_ROUTERS = (
    guests_route.router,
    messages_route.chatroom_router,
    attachments_route.chatroom_router,
    canvas_route.router,
)


def _app(sessionmaker: async_sessionmaker[AsyncSession], principal: Principal | None) -> FastAPI:
    return route_app(sessionmaker, principal, _ROUTERS)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return https_client(app)


def _guest(guest_id: uuid.UUID, room: Room) -> Principal:
    return guest_principal(guest_id, room.chatroom_id)


async def _join(
    sessionmaker: async_sessionmaker[AsyncSession], room: Room, name: str, browser_id: str | None = None
) -> Any:
    async with sessionmaker() as session:
        result = await GuestSessionService(session).create_or_resume(
            chatroom_id=room.chatroom_id, guest_token=_GUEST_TOKEN, display_name=name, browser_id=browser_id
        )
        await session.commit()
    return result


async def _remove(
    sessionmaker: async_sessionmaker[AsyncSession], room: Room, guest_id: uuid.UUID, *, ban: bool
) -> None:
    async with sessionmaker() as session:
        await GuestSessionService(session).remove(
            chatroom_id=room.chatroom_id, guest_session_id=guest_id, ban=ban, actor_user_id=room.owner_user_id
        )
        await session.commit()


def _problem(response: httpx.Response) -> str:
    return str(response.json().get("type", ""))


@pytest.mark.usefixtures("_quiet_side_effects")
class TestRemovalIsEnforcedEverywhere:
    """AC-1 and AC-7: every surface refuses the removed session, and its past
    messages keep their label."""

    async def test_send_list_canvas_upload_and_refresh_refuse_a_removed_guest(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        cid = room.chatroom_id
        async with _client(_app(sessionmaker, None)) as public:
            joined = await public.post(
                f"/api/guest/{cid}/{_GUEST_TOKEN}/session", json={"display_name": "Ann", "browser_id": "br-a"}
            )
            assert joined.status_code == 200, joined.text
            guest_id = uuid.UUID(joined.json()["guest_session_id"])

            async with _client(_app(sessionmaker, _guest(guest_id, room))) as client:
                sent = await client.post(f"/api/chatrooms/{cid}/messages", json={"content_md": "hello"})
                assert sent.status_code == 201, sent.text

                await _remove(sessionmaker, room, guest_id, ban=False)

                refusals = {
                    "send": await client.post(f"/api/chatrooms/{cid}/messages", json={"content_md": "again"}),
                    "list": await client.get(f"/api/chatrooms/{cid}/messages"),
                    "canvas": await client.get(f"/api/chatrooms/{cid}/canvas"),
                    "upload": await client.post(
                        f"/api/chatrooms/{cid}/attachments", files={"file": ("n.txt", b"n", "text/plain")}
                    ),
                    "rename": await client.put(
                        f"/api/guest/session/{guest_id}/display-name", json={"display_name": "Annie"}
                    ),
                }
            refresh = await public.post(f"/api/guest/{cid}/refresh")

        for surface, response in {**refusals, "refresh": refresh}.items():
            assert response.status_code == 403, (surface, response.text)
            assert _problem(response).endswith("/conversation/guest-removed"), (surface, response.text)

        # AC-7: the row is kept, so the message still has its sender and label.
        async with sessionmaker() as read:
            labels = dict(await GuestSessionRepository(read).list_labels(cid))
            sender = (
                await read.execute(
                    sa.select(ct.messages.c.sender_id).where(ct.messages.c.id == uuid.UUID(sent.json()["id"]))
                )
            ).scalar_one()
        assert sender == guest_id
        assert labels[guest_id] == "Ann"

    async def test_moderator_actions_are_audited_with_the_moderator_as_actor(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-8."""
        joined = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        await _remove(sessionmaker, room, joined.guest_session_id, ban=True)
        async with sessionmaker() as session:
            service = GuestSessionService(session)
            (ban,) = await service.list_bans(room.chatroom_id)
            await service.unban(chatroom_id=room.chatroom_id, ban_id=ban.id, actor_user_id=room.owner_user_id)
            await service.rotate_link(chatroom_id=room.chatroom_id, actor_user_id=room.owner_user_id)
            await session.commit()

        async with sessionmaker() as read:
            actions = (
                (
                    await read.execute(
                        sa.select(audit_logs.c.action).where(audit_logs.c.actor_user_id == room.owner_user_id)
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(actions) == sorted(
            ["guest.session.removed", "guest.banned", "guest.unbanned", "chatroom.guest_link.rotated"]
        )


class TestRejoin:
    async def test_a_removed_unbanned_guest_rejoins_as_a_new_session(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-2."""
        first = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        await _remove(sessionmaker, room, first.guest_session_id, ban=False)

        again = await _join(sessionmaker, room, "Ann", browser_id="br-a")

        assert again.is_resuming is False
        assert again.guest_session_id != first.guest_session_id

    async def test_a_banned_browser_is_refused_until_unbanned(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-3 and AC-4 (unban restores joining)."""
        first = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        await _remove(sessionmaker, room, first.guest_session_id, ban=True)

        with pytest.raises(GuestRemoved):
            await _join(sessionmaker, room, "Ann", browser_id="br-a")
        # Another browser is a new guest: the documented limit of a ban (Q-1).
        other = await _join(sessionmaker, room, "Ann", browser_id="br-b")
        assert other.is_resuming is False

        async with sessionmaker() as session:
            service = GuestSessionService(session)
            (ban,) = await service.list_bans(room.chatroom_id)
            await service.unban(chatroom_id=room.chatroom_id, ban_id=ban.id, actor_user_id=room.owner_user_id)
            await session.commit()

        back = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        assert back.guest_session_id != first.guest_session_id

    async def test_a_ban_from_an_old_session_ends_the_rejoined_one(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """Code review finding 1: removed, rejoined from the same browser, then
        banned through the old session's message, the guest must lose the new
        session too; refresh never consults bans."""
        first = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        await _remove(sessionmaker, room, first.guest_session_id, ban=False)
        second = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        assert second.guest_session_id != first.guest_session_id

        async with sessionmaker() as session:
            result = await GuestSessionService(session).remove(
                chatroom_id=room.chatroom_id,
                guest_session_id=first.guest_session_id,
                ban=True,
                actor_user_id=room.owner_user_id,
            )
            await session.commit()
        assert result.removed_session_ids == (second.guest_session_id,)

        async with sessionmaker() as session:
            with pytest.raises(GuestRemoved):
                await GuestSessionService(session).refresh(
                    chatroom_id=room.chatroom_id, refresh_token=second.refresh_token
                )
            await session.rollback()

    async def test_the_ban_row_stores_a_hash_and_survives_the_session_purge(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-4: bans outlive guest-session retention."""
        first = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        await _remove(sessionmaker, room, first.guest_session_id, ban=True)

        async with sessionmaker() as session:
            await session.execute(
                ct.guest_sessions.update()
                .where(ct.guest_sessions.c.id == first.guest_session_id)
                .values(last_seen_at=now() - timedelta(days=31))
            )
            purged = await GuestSessionRepository(session).delete_older_than(now() - timedelta(days=30))
            await session.commit()
        assert purged >= 1

        async with sessionmaker() as read:
            rows = (
                await read.execute(
                    sa.select(ct.chatroom_guest_bans).where(
                        ct.chatroom_guest_bans.c.chatroom_id == room.chatroom_id
                    )
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].browser_id_hash not in (None, "br-a")
        assert rows[0].display_name == "Ann"

        with pytest.raises(GuestRemoved):
            await _join(sessionmaker, room, "Ann", browser_id="br-a")


class TestCap:
    async def test_removed_sessions_do_not_count_toward_the_cap(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3a."""
        settings = SimpleNamespace(limits=SimpleNamespace(max_guests_per_chatroom=1))
        monkeypatch.setattr(guest_service_mod, "get_settings", lambda: settings)

        first = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        with pytest.raises(GuestCapReached):
            await _join(sessionmaker, room, "Ben", browser_id="br-b")

        await _remove(sessionmaker, room, first.guest_session_id, ban=False)
        ben = await _join(sessionmaker, room, "Ben", browser_id="br-b")
        assert ben.is_resuming is False


class TestRotation:
    async def test_the_old_link_refuses_new_entries_and_joined_guests_keep_refreshing(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-5."""
        joined = await _join(sessionmaker, room, "Ann", browser_id="br-a")
        async with sessionmaker() as session:
            rotated = await GuestSessionService(session).rotate_link(
                chatroom_id=room.chatroom_id, actor_user_id=room.owner_user_id
            )
            await session.commit()
        assert rotated.guest_token != _GUEST_TOKEN

        with pytest.raises(GuestTokenInvalid):
            await _join(sessionmaker, room, "Ben", browser_id="br-b")
        async with sessionmaker() as session:
            refreshed = await GuestSessionService(session).refresh(
                chatroom_id=room.chatroom_id, refresh_token=joined.refresh_token
            )
            await session.commit()
        assert refreshed.guest_session_id == joined.guest_session_id

        async with sessionmaker() as session:
            newcomer = await GuestSessionService(session).create_or_resume(
                chatroom_id=room.chatroom_id,
                guest_token=rotated.guest_token,
                display_name="Ben",
                browser_id="br-b",
            )
            await session.commit()
        assert newcomer.is_resuming is False


# -- AC-9: the migration in both directions, on a throwaway database --

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")


@pytest.fixture
def scratch_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Engine]:
    if not _SCRATCH_URL:
        pytest.skip("SMAP_SCRATCH_DATABASE_URL is not set; this test migrates and drops schema.")

    from app.config.settings import get_settings

    monkeypatch.setenv("SMAP_DB_DSN", _SCRATCH_URL)
    get_settings.cache_clear()
    try:
        configured = get_settings().database.dsn
        if configured != _SCRATCH_URL:
            pytest.fail(f"refusing to run: alembic would migrate {configured!r}, not the scratch database.")
        engine = sa.create_engine(_SCRATCH_URL.replace("+asyncpg", "+psycopg"))
        try:
            with engine.begin() as reset:
                reset.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                reset.execute(sa.text("CREATE SCHEMA public"))
            yield engine
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _schema(engine: sa.engine.Engine) -> tuple[bool, bool]:
    with engine.connect() as conn:
        column = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'guest_sessions' AND column_name = 'revoked_at'"
            )
        ).first()
        table = conn.execute(sa.text("SELECT to_regclass('public.chatroom_guest_bans')")).scalar()
    return column is not None, table is not None


def test_migration_0099_upgrades_and_downgrades_cleanly(scratch_engine: sa.engine.Engine) -> None:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "0098_guest_identity_columns")
    assert _schema(scratch_engine) == (False, False)

    command.upgrade(cfg, "0099_guest_removal_and_bans")
    assert _schema(scratch_engine) == (True, True)

    command.downgrade(cfg, "0098_guest_identity_columns")
    assert _schema(scratch_engine) == (False, False)

    command.upgrade(cfg, "0099_guest_removal_and_bans")
    assert _schema(scratch_engine) == (True, True)
