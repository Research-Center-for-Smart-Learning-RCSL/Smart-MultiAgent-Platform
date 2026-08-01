"""`audit.emit(isolated=True)` — the savepoint the agent tool path needs.

Unit coverage of the contract; that the savepoint genuinely keeps a real Postgres
transaction usable is proven against a live session in
``tests/integration/test_tool_db_failure_does_not_poison_the_turn.py`` (AC-2), which
is the only place it can be.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from shared_kernel import audit


class _Savepoint:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Savepoint:
        self._session.savepoints_opened += 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            self._session.savepoints_rolled_back += 1
        return False


class _Session:
    """Minimal stand-in: records savepoint use and can fail its INSERT."""

    def __init__(self, *, fail: bool = False) -> None:
        self.info: dict[str, Any] = {}
        self.executed = 0
        self.savepoints_opened = 0
        self.savepoints_rolled_back = 0
        self._fail = fail

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    async def execute(self, _stmt: Any) -> None:
        self.executed += 1
        if self._fail:
            raise OperationalError("INSERT INTO audit_logs", {}, Exception("boom"))


def _event() -> audit.AuditEvent:
    return audit.AuditEvent(action="mcp.tool_invoked", resource_type="agent")


@pytest.mark.asyncio
async def test_isolated_emit_reports_a_failed_write_instead_of_raising() -> None:
    session = _Session(fail=True)

    assert await audit.emit(session, _event(), isolated=True) is False  # type: ignore[arg-type]

    assert session.savepoints_opened == 1
    assert session.savepoints_rolled_back == 1
    # No tail event for a row that does not exist.
    assert "_audit_tail_queue" not in session.info
    # The loss is counted on the session, which is how a tool several frames above
    # learns its invocation went unrecorded (AC-4).
    assert audit.write_failures(session) == 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_isolated_emit_still_writes_and_queues_the_tail_event() -> None:
    session = _Session()

    assert await audit.emit(session, _event(), isolated=True) is True  # type: ignore[arg-type]

    assert session.executed == 1
    assert session.savepoints_opened == 1
    assert session.savepoints_rolled_back == 0
    assert len(session.info["_audit_tail_queue"]) == 1
    assert audit.write_failures(session) == 0  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_default_stays_join_the_callers_transaction_and_raise() -> None:
    """Request handlers must keep the audit row in the domain unit of work, and
    must not pay a savepoint round-trip per row."""
    session = _Session(fail=True)

    with pytest.raises(OperationalError):
        await audit.emit(session, _event())  # type: ignore[arg-type]

    assert session.savepoints_opened == 0
