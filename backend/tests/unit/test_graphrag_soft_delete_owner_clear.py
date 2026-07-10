"""Regression: soft-deleting a Concept Map config must clear its owner
columns, not just deleted_at.

The partial unique indexes on owner_chatroom_id/owner_agent_group_id/
owner_workspace_id (migration 0043_graphrag_owner.py) are only
`WHERE {col} IS NOT NULL` -- not additionally scoped to `deleted_at IS
NULL`. A soft-deleted row that still has its owner column populated
permanently blocks any future create() for that same owner (409
GraphRagConfigAlreadyExists forever), even though list_owner_options()
already shows the owner as available again once deleted_at is set.

A mocked AsyncSession can't enforce the real unique index, so this pins
the fix at the SQL-statement level: the UPDATE's SET clause must clear all
three owner columns alongside deleted_at. The end-to-end proof against a
real partial unique index lives in
tests/wiring/test_graphrag_owner_resolution.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository


class _CapturingDb:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> None:
        self.statements.append(stmt)


@pytest.mark.asyncio
async def test_soft_delete_clears_all_three_owner_columns() -> None:
    db = _CapturingDb()
    repo = GraphRagConfigRepository(db)  # type: ignore[arg-type]

    await repo.soft_delete(uuid.uuid4())

    assert len(db.statements) == 1
    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "deleted_at" in compiled
    for col in ("owner_chatroom_id", "owner_agent_group_id", "owner_workspace_id"):
        assert f"{col}=NULL" in compiled.replace(" ", ""), (
            f"soft_delete's UPDATE must clear {col}, or a soft-deleted row's "
            "still-populated owner column permanently blocks recreating a "
            "config for that same owner (partial unique index collision)"
        )
