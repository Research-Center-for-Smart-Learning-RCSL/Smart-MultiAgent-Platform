"""AC-2 — a failed audit insert must not abort the turn's transaction.

The defect this proves fixed (F-6) is not visible in unit control flow: the catch
around a tool's audit write converts the Python exception, but Postgres has already
marked the *whole* transaction aborted, so every later statement on the shared turn
session raises ``InFailedSqlTransaction``. The turn then streams a complete answer to
the room and dies persisting it. Only a real session against a real Postgres can show
that, which is why this lives here and not beside the unit tests.

``audit_logs.actor_user_id`` carries an FK to ``users.id`` (migration 0004_audit), so a
non-existent actor is a deterministic server-side failure -- no patched ``execute``, no
statement timeout, no simulation of the thing under test.

The rows that are meant to *survive* carry no actor at all. They cannot: the FK is
``ON DELETE SET NULL``, which is an UPDATE, and ``audit_logs`` refuses UPDATE outside
the retention role (R17.04) -- so a row pointing at a fixture's user makes that
fixture's teardown fail. For the same reason nothing here is cleaned up, and each run
tags its rows with a unique action string instead of counting them.

Requires a Postgres reachable via ``settings.database.dsn`` with migrations applied --
the ``backend-integration`` CI job's environment.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from shared_kernel import audit

# Real Postgres required (see module docstring).
pytestmark = pytest.mark.db

# The `sessionmaker` fixture comes from tests/integration/conftest.py.


def _ok(action: str) -> audit.AuditEvent:
    return audit.AuditEvent(action=action, resource_type="agent")


def _doomed(action: str) -> audit.AuditEvent:
    """An event whose INSERT the server rejects: the actor does not exist."""
    return audit.AuditEvent(action=action, actor_user_id=uuid.uuid4(), resource_type="agent")


@pytest.mark.asyncio
async def test_isolated_audit_failure_leaves_the_turn_transaction_usable(
    sessionmaker: async_sessionmaker,
) -> None:
    action = f"itest.tool_invoked.{uuid.uuid4()}"

    async with sessionmaker() as session:
        # Stands in for the turn's pre-reply work: written, not yet committed.
        assert await audit.emit(session, _ok(action)) is True

        # The tool's own audit write fails.
        assert await audit.emit(session, _doomed(f"{action}.doomed"), isolated=True) is False

        # The property under test: the transaction is still usable, so the reply
        # and the turn_finished row can still be written.
        assert (await session.execute(sa.text("SELECT 1"))).scalar_one() == 1
        assert await audit.emit(session, _ok(f"{action}.after")) is True
        await session.commit()

    async with sessionmaker() as check:
        rows = (
            await check.execute(
                sa.select(audit.audit_logs.c.action).where(audit.audit_logs.c.action.like(f"{action}%"))
            )
        ).scalars()
        # The earlier write survived the failure, and the lost row is only the one.
        assert sorted(rows) == [action, f"{action}.after"]


@pytest.mark.asyncio
async def test_without_the_savepoint_the_same_failure_poisons_the_session(
    sessionmaker: async_sessionmaker,
) -> None:
    """The control: catching the exception is not enough, the savepoint is.

    Without this, the test above would pass just as well against a "fix" that only
    swallowed the error — which is exactly the defect.
    """
    action = f"itest.control.{uuid.uuid4()}"

    async with sessionmaker() as session:
        await audit.emit(session, _ok(action))
        with pytest.raises(DBAPIError):
            await audit.emit(session, _doomed(f"{action}.doomed"))
        # Catching that exception is all a `try/except` around the tool call can
        # do, and it is not enough: the reply write below it raises too.
        with pytest.raises(DBAPIError) as caught:
            await session.execute(sa.text("SELECT 1"))
        # asyncpg's error class is wrapped by SQLAlchemy's DBAPI shim, so the
        # driver exception name only survives in the message.
        assert "InFailedSQLTransaction" in str(caught.value.orig)
