"""`messages.created_at` is the insert time, not the transaction start.

Dossier ``docs/tasks/2026-10-07-agent-reply-delivery-gaps``: an agent reply is
inserted in a transaction opened when the provider stream began, and clients
fetch new messages with a ``since`` cursor ordered by ``created_at``. A default
of ``now()`` back-dates the reply below any message committed during the stream,
so the cursor has already passed it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from contexts.conversation.infrastructure import tables as ct

pytestmark = pytest.mark.db


async def test_reply_in_long_transaction_is_stamped_after_message_committed_meanwhile(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, _ = project
    workspace_id, room_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as setup:
        await setup.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="clock")
        )
        await setup.execute(
            ct.chatrooms.insert().values(
                id=room_id, workspace_id=workspace_id, name="clock", guest_token=str(uuid.uuid4())
            )
        )
        await setup.commit()

    async with sessionmaker() as stream, sessionmaker() as other:
        # Opens the reply's transaction, as the router's first read does at stream start.
        await stream.execute(sa.text("SELECT 1"))

        sent = (
            await other.execute(
                ct.messages.insert()
                .values(chatroom_id=room_id, sender_type="user", sender_id=uuid.uuid4(), content_md="S")
                .returning(ct.messages.c.created_at)
            )
        ).scalar_one()
        await other.commit()

        reply = (
            await stream.execute(
                ct.messages.insert()
                .values(chatroom_id=room_id, sender_type="agent", sender_id=uuid.uuid4(), content_md="R")
                .returning(ct.messages.c.created_at)
            )
        ).scalar_one()
        await stream.commit()

    assert reply > sent


# -- AC-6: the migration in both directions, on a throwaway database --

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


def _created_at_default(engine: sa.engine.Engine) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                sa.text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'messages' AND column_name = 'created_at'"
                )
            ).scalar_one()
        )


def test_migration_0102_upgrades_and_downgrades_cleanly(scratch_engine: sa.engine.Engine) -> None:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "0099_guest_removal_and_bans")
    assert _created_at_default(scratch_engine) == "now()"

    command.upgrade(cfg, "0102_messages_created_at_clock_timestamp")
    assert _created_at_default(scratch_engine) == "clock_timestamp()"

    command.downgrade(cfg, "0099_guest_removal_and_bans")
    assert _created_at_default(scratch_engine) == "now()"

    command.upgrade(cfg, "0102_messages_created_at_clock_timestamp")
    assert _created_at_default(scratch_engine) == "clock_timestamp()"
