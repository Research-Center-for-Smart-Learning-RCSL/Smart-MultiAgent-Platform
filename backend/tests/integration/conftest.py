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
async def project(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """(project_id, owner_user_id) -- a real project + user row, the common FK
    target every real-DB integration test needs (key groups, embedding pins,
    ...). Owned by a throwaway user, since ``created_by_user_id`` is NOT NULL;
    dropping that user cascades the project and everything hung off it.
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
            await cleanup.execute(projects_t.delete().where(projects_t.c.id == pid))
            await cleanup.execute(users_t.delete().where(users_t.c.id == uid))
            await cleanup.commit()


@pytest.fixture
async def project_id(project: tuple[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    """Bare project id, for tests that don't need the owning user id too."""
    return project[0]
