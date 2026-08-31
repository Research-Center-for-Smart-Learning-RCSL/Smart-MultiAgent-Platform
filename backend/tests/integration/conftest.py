"""K.7 deliverable 2 — make the integration marker real.

The audit found the marker filter was a no-op: ``-m "not integration"`` excluded
nothing because no test under ``tests/integration/`` carried the marker — the
"integration" suite was a directory convention only, so the fast CI job silently
ran the whole tree and the filter protected nothing.

Rather than annotate each file by hand (and re-annotate every new one), this
conftest applies ``@pytest.mark.integration`` to every test physically located
under ``tests/integration/`` at collection time. After this, ``-m "not
integration"`` genuinely excludes this directory and the dedicated
``backend-integration`` CI job (``-m integration``) owns running it.
"""

from __future__ import annotations

import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.infrastructure.tables import projects as projects_t

_INTEGRATION_DIR = pathlib.Path(__file__).parent.resolve()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = getattr(item, "path", None)
        if path is None:
            continue
        if _INTEGRATION_DIR in pathlib.Path(path).resolve().parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh engine/sessionmaker against the real DSN, for tests that need to
    prove behaviour a faked repository can't -- a real advisory lock, a real
    join condition, a real cascading delete."""
    from app.config.settings import get_settings

    engine = create_async_engine(get_settings().database.dsn)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def permissive_email_domain_policy(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """An `active`, `off` policy row plus a cleared cache (R19a.13).

    The row is created by an ordered startup initializer, which no integration
    test runs, and the reader raises rather than defaulting to "no restriction"
    when the row is missing -- deliberately, since that default is the failure
    this whole control exists to end. Any test that registers, changes an email
    or provisions an account therefore has to stand the row up, exactly as a
    booted application would have.

    `off` because these tests are about onboarding, not about the policy. Any
    test asserting the policy's own behaviour sets its own row.
    """
    from contexts.identity.application.email_domain_policy_reader import reset_process_cache
    from contexts.identity.infrastructure.email_domain_mirror import KEY as MIRROR_KEY
    from contexts.identity.infrastructure.tables import EMAIL_DOMAIN_POLICY_ID
    from contexts.identity.infrastructure.tables import email_domain_policies as policy_t
    from shared_kernel.auth.clients import get_redis

    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO email_domain_policies (id, mode, rollout_state, version) "
                "VALUES (:id, 'off', 'active', 1) "
                "ON CONFLICT (id) DO UPDATE SET mode = 'off', rollout_state = 'active'"
            ),
            {"id": EMAIL_DOMAIN_POLICY_ID},
        )
        await session.commit()
    # Both caches: the process-wide snapshot survives between tests in one
    # worker, and a mirror value written by an earlier test would otherwise
    # answer for this one.
    reset_process_cache()
    await get_redis().delete(MIRROR_KEY)
    try:
        yield
    finally:
        reset_process_cache()
        async with sessionmaker() as cleanup:
            await cleanup.execute(policy_t.delete())
            await cleanup.commit()
        await get_redis().delete(MIRROR_KEY)


@pytest.fixture
async def project(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """(project_id, owner_user_id) -- a real project + user row, the common FK
    target every real-DB integration test needs (key groups, embedding pins,
    ...). Owned by a throwaway user, since ``created_by_user_id`` is NOT NULL;
    dropping that user cascades the project and everything hung off it.

    Teardown clears this user's ``audit_logs`` rows first, and has to: the FK is
    ``ON DELETE SET NULL`` (migration 0004), the append-only trigger rejects the
    UPDATE that cascade performs, and the failure surfaces as a teardown ERROR on
    a test whose assertions all passed. Any test that emits an audit event as
    this user hits it, so the fixture owns the cleanup rather than each test. The
    ``SET ROLE`` is the same deliberate, NOINHERIT bypass the retention worker
    uses (``audit_query_service.purge_old_logs``), not a new privilege.
    """
    pid, uid = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            users_t.insert().values(
                id=uid,
                email=f"itest-{uid}@test.invalid",
                password_hash="x",  # never authenticated against
            )
        )
        await session.execute(
            projects_t.insert().values(
                id=pid,
                name="itest",
                owner_user_id=uid,
                created_by_user_id=uid,
            )
        )
        await session.commit()
    try:
        yield pid, uid
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("SET ROLE smap_audit_retention"))
            try:
                await cleanup.execute(text("DELETE FROM audit_logs WHERE actor_user_id = :uid"), {"uid": uid})
            finally:
                await cleanup.execute(text("RESET ROLE"))
            await cleanup.execute(projects_t.delete().where(projects_t.c.id == pid))
            await cleanup.execute(users_t.delete().where(users_t.c.id == uid))
            await cleanup.commit()


@pytest.fixture
async def project_id(project: tuple[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    """Bare project id, for tests that don't need the owning user id too."""
    return project[0]
