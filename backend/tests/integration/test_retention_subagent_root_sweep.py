"""F-17 — the orphaned-subagent-root sweep against real Postgres.

Spec: ``docs/tasks/2026-07-22-retention-sweep-fixes/spec.md`` §4 (F-17), §8 T-2.

The unit tests in ``test_retention_deep.py`` pin the SQL shape (predicate
before ``LIMIT``, an ``ORDER BY``) against a mocked session, but a mock has no
query planner and cannot show the pre-fix bug actually starves the sweep: an
unordered ``LIMIT 500`` sampling live rows first and leaving every orphan
outside the sample. These tests populate a real backlog past the 500-row
limit and prove the fix drains it, not just that its SQL text looks right.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agents_t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t
from contexts.orchestration.infrastructure.tables import (
    agent_instances as agent_instances_t,
)
from contexts.orchestration.infrastructure.tables import workflow_runs as workflow_runs_t

# Real Postgres required (see module docstring) -- routed to the backend-db CI job.
pytestmark = pytest.mark.db

# `sessionmaker` and `project_id` fixtures come from tests/integration/conftest.py.


@pytest.fixture
async def agent_id(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> AsyncIterator[uuid.UUID]:
    """One real agent row to hang synthetic-root instances off."""
    aid, kg_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            key_groups_t.insert().values(id=kg_id, project_id=project_id, name="itest-kg"),
        )
        await session.execute(
            agents_t.agents.insert().values(
                id=aid,
                project_id=project_id,
                name="itest-agent",
                model_hint="claude",
                key_group_id=kg_id,
            ),
        )
        await session.commit()
    try:
        yield aid
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agent_instances_t.delete().where(agent_instances_t.c.agent_id == aid))
            await cleanup.execute(agents_t.agents.delete().where(agents_t.agents.c.id == aid))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == kg_id))
            await cleanup.commit()


@pytest.fixture
async def live_workflow_run_id(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> AsyncIterator[uuid.UUID]:
    wr_id = uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(workflow_runs_t.insert().values(id=wr_id, project_id=project_id))
        await session.commit()
    try:
        yield wr_id
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(workflow_runs_t.delete().where(workflow_runs_t.c.id == wr_id))
            await cleanup.commit()


def _synthetic_root(*, agent_id: uuid.UUID, workflow_run_id: uuid.UUID) -> dict:
    return {
        "id": uuid.uuid4(),
        "agent_id": agent_id,
        "parent_id": None,
        "run_context": {"synthetic_root": True, "workflow_run_id": str(workflow_run_id)},
    }


async def _orphan_count(session: AsyncSession) -> int:
    row = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM agent_instances "
                "WHERE parent_id IS NULL "
                "  AND run_context->>'synthetic_root' = 'true' "
                "  AND run_context->>'workflow_run_id' IS NOT NULL "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM workflow_runs wr "
                "    WHERE wr.id = (run_context->>'workflow_run_id')::uuid"
                "  )"
            )
        )
    ).scalar_one()
    return row


async def test_orphans_beyond_the_limit_are_reaped(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_id: uuid.UUID,
    live_workflow_run_id: uuid.UUID,
) -> None:
    """T-2 (AC-3): 1000 live roots first, then 20 orphans -- the exact seeding
    order the spec's §4 reproduction relies on to make the pre-fix unordered
    `LIMIT 500` deterministically sample only the live rows."""
    from app.workers.tasks import retention as ret

    async with sessionmaker() as session:
        await session.execute(
            agent_instances_t.insert(),
            [_synthetic_root(agent_id=agent_id, workflow_run_id=live_workflow_run_id) for _ in range(1000)],
        )
        orphan_workflow_run_id = uuid.uuid4()
        await session.execute(
            agent_instances_t.insert(),
            [
                _synthetic_root(agent_id=agent_id, workflow_run_id=orphan_workflow_run_id)
                for _ in range(20)
            ],
        )
        await session.commit()

    async with sessionmaker() as session, session.begin():
        count = await ret._sweep_orphaned_subagent_roots(session)

    assert count == 20
    async with sessionmaker() as session:
        assert await _orphan_count(session) == 0
        live_remaining = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(agent_instances_t)
                .where(agent_instances_t.c.agent_id == agent_id)
            )
        ).scalar_one()
    assert live_remaining == 1000, "no live-run root may be touched"


async def test_children_are_deleted_before_roots(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_id: uuid.UUID,
) -> None:
    """Characterization guard (T-2): the rewrite must not regress the
    children-before-roots delete order -- `parent_id` is `ON DELETE SET NULL`,
    so deleting a root first would orphan its children as parentless rows."""
    from app.workers.tasks import retention as ret

    orphan_workflow_run_id = uuid.uuid4()
    root_id = uuid.uuid4()
    child_ids = [uuid.uuid4(), uuid.uuid4()]
    async with sessionmaker() as session:
        await session.execute(
            agent_instances_t.insert().values(
                id=root_id,
                agent_id=agent_id,
                parent_id=None,
                run_context={"synthetic_root": True, "workflow_run_id": str(orphan_workflow_run_id)},
            )
        )
        await session.execute(
            agent_instances_t.insert(),
            [{"id": cid, "agent_id": agent_id, "parent_id": root_id, "run_context": {}} for cid in child_ids],
        )
        await session.commit()

    async with sessionmaker() as session, session.begin():
        count = await ret._sweep_orphaned_subagent_roots(session)

    assert count == 1
    async with sessionmaker() as session:
        remaining = (
            await session.execute(
                sa.select(agent_instances_t.c.id).where(
                    agent_instances_t.c.id.in_([root_id, *child_ids]),
                )
            )
        ).all()
    assert remaining == [], "both children and the root must be gone"
    async with sessionmaker() as session:
        parentless_leaks = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(agent_instances_t)
                .where(
                    agent_instances_t.c.agent_id == agent_id,
                    agent_instances_t.c.parent_id.is_(None),
                    agent_instances_t.c.run_context == {},
                )
            )
        ).scalar_one()
    assert parentless_leaks == 0, "no child may be left orphaned by a root-first delete"
