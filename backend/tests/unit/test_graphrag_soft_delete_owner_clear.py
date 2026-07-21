"""Regression: soft-deleting a Concept Map config must set only deleted_at.

Clearing the owner columns as well (the earlier workaround for the owner
partial unique indexes not being scoped to live rows) violates the
exactly-one-owner CHECK added by migration 0044_graphrag_drop_agent_id --
owner_kind stays NOT NULL, so a row with all three owner FKs NULL satisfies
no branch of the constraint and Postgres rejects the UPDATE.

Migration 0061_graphrag_owner_index_live_only scoped those indexes to
`deleted_at IS NULL`, so keeping the owner populated blocks nothing. A mocked
AsyncSession can't enforce the real constraint, so this pins the fix at the
SQL-statement level; the end-to-end proof lives in
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
async def test_soft_delete_keeps_the_owner_columns() -> None:
    db = _CapturingDb()
    repo = GraphRagConfigRepository(db)  # type: ignore[arg-type]

    await repo.soft_delete(uuid.uuid4())

    assert len(db.statements) == 1
    compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True})).replace(" ", "")
    assert "deleted_at" in compiled
    for col in ("owner_chatroom_id", "owner_agent_group_id", "owner_workspace_id"):
        assert f"{col}=NULL" not in compiled, (
            f"soft_delete's UPDATE must not clear {col}: owner_kind stays NOT NULL, "
            "so an owner-less row violates ck_graphrag_configs_owner (migration 0044)"
        )
