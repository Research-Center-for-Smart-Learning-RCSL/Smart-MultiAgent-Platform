"""Schema-level guarantees of migration 0076 (AC-1, AC-2).

These are properties only a real PostgreSQL can have, so the unit tier cannot see
them: it compiles statements with ``literal_binds`` and never executes one, so a
CHECK constraint that was never created and a CHECK constraint that rejects the
right rows are indistinguishable there.

Four things are pinned:

- ``scope`` and ``project_id`` cannot disagree, in either direction. This is what
  stops a half-converted row -- a platform type still pointing at a project, or a
  project type with no owner -- from existing at all.
- Two live platform types cannot share a key. The pre-existing
  ``(project_id, key)`` partial-unique cannot do this once ``project_id`` is
  nullable, because PostgreSQL treats every NULL as distinct.
- The downgrade refuses while platform rows exist, rather than deleting them.
- Deleting a platform type leaves no opt-in row behind. This one is about the
  *interaction* between 0076's FK and the soft delete above it rather than about
  the schema alone, and it lives here for the same reason as the rest: the unit
  tier cannot tell a DELETE that runs from an ``ON DELETE CASCADE`` that silently
  never fires.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.errors import ActivityTypeKeyConflict
from contexts.activities.domain.models import ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.activities.interfaces.facade import ActivitiesFacade

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0076_platform_activity_types.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0076_platform_activity_types", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0076 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0076)


async def _insert_platform_type(session: AsyncSession, *, key: str) -> uuid.UUID:
    return await ActivityTypeRepository(session).create(
        project_id=None,
        key=key,
        name=f"platform {key}",
        payload_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
        retention_days=None,
        expose_payload_to_agent=True,
        echo_includes_content=False,
        scope=ActivityTypeScope.PLATFORM,
    )


@pytest.fixture
async def platform_type_key(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """A key unique to this test run, cleaned up afterwards.

    Randomised because the platform key space is global: a fixed key would make
    two concurrent runs of this suite against one database fail each other, and
    that failure would look exactly like the bug under test.
    """
    key = f"ci-platform-{uuid.uuid4().hex[:12]}"
    try:
        yield key
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(
                text("DELETE FROM activity_types WHERE project_id IS NULL AND key LIKE :k"),
                {"k": f"{key}%"},
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_a_platform_row_may_hold_no_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    async with sessionmaker() as session:
        type_id = await _insert_platform_type(session, key=platform_type_key)
        await session.commit()

    async with sessionmaker() as session:
        stored = await ActivityTypeRepository(session).get(type_id)

    assert stored is not None
    assert stored.project_id is None
    assert stored.scope is ActivityTypeScope.PLATFORM


@pytest.mark.asyncio
async def test_a_platform_row_may_not_name_a_project(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    platform_type_key: str,
) -> None:
    """ck_activity_types_project_scope, forward direction."""
    # `pytest.raises` is a sync context manager only (pytest 9 returns a RaisesExc,
    # which has no __aenter__), so it must nest inside the session rather than
    # share its `async with`.
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError, match="ck_activity_types_project_scope"):
            await session.execute(
                text(
                    "INSERT INTO activity_types (project_id, scope, key, name, validator_kind) "
                    "VALUES (:pid, 'platform', :k, 'half converted', 'in_process')"
                ),
                {"pid": project_id, "k": platform_type_key},
            )


@pytest.mark.asyncio
async def test_a_project_row_may_not_be_ownerless(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    """ck_activity_types_project_scope, reverse direction.

    The direction that would otherwise be easy to miss: NULL means both "platform"
    and "someone forgot", and only this half of the equivalence tells them apart.
    """
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError, match="ck_activity_types_project_scope"):
            await session.execute(
                text(
                    "INSERT INTO activity_types (project_id, scope, key, name, validator_kind) "
                    "VALUES (NULL, 'project', :k, 'orphan', 'in_process')"
                ),
                {"k": platform_type_key},
            )


@pytest.mark.asyncio
async def test_an_unknown_scope_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError, match="ck_activity_types_scope"):
            await session.execute(
                text(
                    "INSERT INTO activity_types (project_id, scope, key, name, validator_kind) "
                    "VALUES (NULL, 'organisation', :k, 'not a scope', 'in_process')"
                ),
                {"k": platform_type_key},
            )


@pytest.mark.asyncio
async def test_two_live_platform_types_cannot_share_a_key(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    """AC-2: the conflict surfaces as the domain 409, not a raw IntegrityError 500."""
    async with sessionmaker() as session:
        await _insert_platform_type(session, key=platform_type_key)
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(ActivityTypeKeyConflict):
            await _insert_platform_type(session, key=platform_type_key)


@pytest.mark.asyncio
async def test_a_soft_deleted_platform_key_is_free_for_reuse(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    """The platform index is partial on ``deleted_at IS NULL``, matching the
    project one — [R30.23] frees a tombstoned key for reuse."""
    async with sessionmaker() as session:
        repo = ActivityTypeRepository(session)
        first = await _insert_platform_type(session, key=platform_type_key)
        await repo.soft_delete(first)
        await session.commit()

    async with sessionmaker() as session:
        second = await _insert_platform_type(session, key=platform_type_key)
        await session.commit()

    assert second != first


@pytest.mark.asyncio
async def test_deleting_a_platform_type_revokes_every_projects_optin(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
    platform_type_key: str,
) -> None:
    """The finding itself, against a real database.

    ``project_activity_type_optins.activity_type_id`` is ``ON DELETE CASCADE``, but
    the type delete is an UPDATE that sets ``deleted_at``, so the cascade has
    nothing to fire on and the authorization row outlives the thing it authorizes.
    Written against the pre-fix tree this fails with one surviving row.
    """
    project_id, owner_id = project

    async with sessionmaker() as session:
        type_id = await _insert_platform_type(session, key=platform_type_key)
        await ProjectActivityTypeOptInRepository(session).add(
            project_id=project_id, activity_type_id=type_id, enabled_by_user_id=owner_id
        )
        await session.commit()

    async with sessionmaker() as session:
        assert await ProjectActivityTypeOptInRepository(session).exists(
            project_id=project_id, activity_type_id=type_id
        )

    async with sessionmaker() as session:
        await ActivitiesFacade(session).delete_platform_type(
            type_id=type_id, actor_user_id=owner_id, actor_ip=None
        )
        await session.commit()

    async with sessionmaker() as session:
        surviving = (
            await session.execute(
                text("SELECT count(*) FROM project_activity_type_optins WHERE activity_type_id = :t"),
                {"t": type_id},
            )
        ).scalar_one()

    assert surviving == 0

    # The count on the audit event, read back from the row that was actually
    # written. The unit tier mocks the result cursor, so nothing there can tell a
    # correctly-read ``RETURNING`` clause from one that yields nothing: the rows
    # would still be deleted and the trail would still claim nobody had enabled
    # the type.
    async with sessionmaker() as session:
        recorded = (
            await session.execute(
                text(
                    "SELECT metadata FROM audit_logs "
                    "WHERE action = 'activity_type.deleted' AND resource_id = :t"
                ),
                {"t": type_id},
            )
        ).scalar_one()

    assert recorded["optins_removed"] == "1"


@pytest.mark.asyncio
async def test_downgrade_refuses_while_a_platform_type_exists(
    sessionmaker: async_sessionmaker[AsyncSession],
    platform_type_key: str,
) -> None:
    """AC-1: data loss on a downgrade is worse than a failed downgrade.

    Calls the guard rather than ``downgrade()`` on purpose — the assertion is
    about the refusal, and executing the real DDL would tear down the schema every
    other test in this job is running against.
    """
    async with sessionmaker() as session:
        await _insert_platform_type(session, key=platform_type_key)
        await session.commit()

    async with sessionmaker() as session:
        conn = await session.connection()

        def _check(sync_conn: object) -> None:
            migration_0076.assert_no_platform_types(sync_conn)

        with pytest.raises(RuntimeError, match="cannot be reversed"):
            await conn.run_sync(_check)


@pytest.mark.asyncio
async def test_downgrade_guard_passes_with_no_platform_rows(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The other half of AC-1: with nothing installed, the reversal is allowed.

    Skips rather than fails if the database happens to hold platform types from a
    parallel test or a seeded example — the guard's negative case is only
    meaningful on an empty platform, and asserting otherwise would make this test
    a flake detector for its own fixtures.
    """
    async with sessionmaker() as session:
        installed = (
            await session.execute(text("SELECT count(*) FROM activity_types WHERE project_id IS NULL"))
        ).scalar_one()
        if installed:
            pytest.skip(f"{installed} platform type(s) present; the empty-platform case cannot be observed")

        conn = await session.connection()

        def _check(sync_conn: object) -> None:
            migration_0076.assert_no_platform_types(sync_conn)

        await conn.run_sync(_check)
