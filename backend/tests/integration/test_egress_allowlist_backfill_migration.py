"""Integration tests for the 0064 egress-allowlist backfill migration (R12.16).

Exercises the migration's ``upgrade()``/``downgrade()`` functions directly
against real fixtures, sharing the alembic ``op`` proxy via
``Operations.context()`` -- the standard way to drive migration code from a
test without a full ``alembic upgrade`` CLI invocation. Requires a real,
provisioned Postgres (``db`` marker) -- the ``sessionmaker`` fixture connects
to the real DSN, which only resolves inside the backend-db CI job.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0064_egress_allowlist_seed_backfill.py"
)
_spec = importlib.util.spec_from_file_location(
    "_migration_0064_egress_allowlist_seed_backfill", _MIGRATION_PATH
)
assert _spec is not None
assert _spec.loader is not None
migration_0064 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0064)


async def _run_migration_fn(session: AsyncSession, fn: Callable[[], None]) -> None:
    conn = await session.connection()

    def _run(sync_conn: object) -> None:
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            fn()

    await conn.run_sync(_run)


@pytest.fixture
async def search_key_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[uuid.UUID]:
    """The fixture project, given one active Tavily search key for the backfill to find."""
    project_id, _owner_id = project
    key_id = uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO search_keys (id, project_id, provider, ciphertext, nonce, "
                "dek_wrapped, ciphertext_hmac, masked_preview, is_active) "
                "VALUES (:id, :pid, 'tavily', :blob, :blob, 'x', :blob, '****', true)"
            ),
            {"id": key_id, "pid": project_id, "blob": b"x"},
        )
        await session.commit()
    try:
        yield project_id
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("DELETE FROM search_keys WHERE id = :id"), {"id": key_id})
            await cleanup.execute(
                text("DELETE FROM mcp_egress_allowlist WHERE project_id = :pid"), {"pid": project_id}
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_upgrade_seeds_host_for_active_search_key_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    search_key_project: uuid.UUID,
) -> None:
    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.upgrade)
        await session.commit()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT hostname, added_by_user_id, note FROM mcp_egress_allowlist WHERE project_id = :pid"
                ),
                {"pid": search_key_project},
            )
        ).all()

    assert [(r.hostname, r.added_by_user_id) for r in rows] == [("api.tavily.com", None)]
    assert rows[0].note == migration_0064._NOTE


@pytest.mark.asyncio
async def test_upgrade_preserves_operator_added_row(
    sessionmaker: async_sessionmaker[AsyncSession],
    search_key_project: uuid.UUID,
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _project_id, owner_id = project
    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO mcp_egress_allowlist (project_id, hostname, added_by_user_id, note) "
                "VALUES (:pid, 'custom.operator.example', :uid, 'manually added by operator')"
            ),
            {"pid": search_key_project, "uid": owner_id},
        )
        await session.commit()

    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.upgrade)
        await session.commit()

    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT added_by_user_id, note FROM mcp_egress_allowlist "
                    "WHERE project_id = :pid AND hostname = 'custom.operator.example'"
                ),
                {"pid": search_key_project},
            )
        ).one()

    assert row.added_by_user_id == owner_id
    assert row.note == "manually added by operator"


@pytest.mark.asyncio
async def test_upgrade_twice_is_idempotent(
    sessionmaker: async_sessionmaker[AsyncSession],
    search_key_project: uuid.UUID,
) -> None:
    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.upgrade)
        await session.commit()
    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.upgrade)
        await session.commit()

    async with sessionmaker() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM mcp_egress_allowlist WHERE project_id = :pid"),
                {"pid": search_key_project},
            )
        ).scalar_one()

    assert count == 1


@pytest.mark.asyncio
async def test_downgrade_removes_only_migration_marked_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
    search_key_project: uuid.UUID,
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _project_id, owner_id = project
    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO mcp_egress_allowlist (project_id, hostname, added_by_user_id, note) "
                "VALUES (:pid, 'custom.operator.example', :uid, 'manually added by operator')"
            ),
            {"pid": search_key_project, "uid": owner_id},
        )
        await session.commit()
    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.upgrade)
        await session.commit()

    async with sessionmaker() as session:
        await _run_migration_fn(session, migration_0064.downgrade)
        await session.commit()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT hostname FROM mcp_egress_allowlist WHERE project_id = :pid"),
                {"pid": search_key_project},
            )
        ).all()

    assert [r.hostname for r in rows] == ["custom.operator.example"]
