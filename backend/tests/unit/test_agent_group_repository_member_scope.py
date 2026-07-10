"""Regression: list_member_agent_ids must exclude soft-deleted agents.

soft-deleting an agent never cleans up its agent_group_members row (no
cascade), so the method -- documented as "Live member agent ids" -- must
filter on the agent's own deleted_at itself, or a deleted agent's chatroom
history keeps being pulled into the group's Concept Map build scope
indefinitely (app/workers/tasks/graphrag.py's delta-scope query feeds this
list straight into its SQL filter).

A mocked AsyncSession can't exercise the real join, so this pins the fix
at the SQL-statement level: the SELECT must join agents and filter on
`agents.deleted_at IS NULL`. The end-to-end proof against a real Postgres
lives in tests/wiring/test_agent_group_repository.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository


class _Result:
    def all(self) -> list[Any]:
        return []


class _CapturingDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> _Result:
        self.statements.append(stmt)
        return _Result()


@pytest.mark.asyncio
async def test_list_member_agent_ids_filters_on_agent_deleted_at() -> None:
    db = _CapturingDb()
    repo = AgentGroupRepository(db)  # type: ignore[arg-type]

    await repo.list_member_agent_ids(uuid.uuid4())

    assert len(db.statements) == 1
    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "JOIN agents" in compiled or "agents.deleted_at" in compiled, (
        "list_member_agent_ids must join agents and filter on deleted_at, "
        "or a soft-deleted agent's still-present membership row keeps being "
        "reported as a live member"
    )
    assert "agents.deleted_at IS NULL" in compiled
