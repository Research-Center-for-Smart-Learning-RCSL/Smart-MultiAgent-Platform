"""``provision_singletons`` must survive the custom-to-generic plan flip.

The ON CONFLICT clause has to name the predicate of the partial unique index
``uq_agent_tools_singleton`` (alembic/versions/0036_agent_tools.py) so Postgres can
infer the index. Inference only succeeds when the predicate is built from Const nodes:
rendered as bind parameters it works for the first five executions of a prepared
statement -- the custom plans, where the planner folds the params -- and then breaks on
the sixth, when ``plan_cache_mode`` switches to a generic plan and the params survive
planning. The result is a 500 on agent creation that looks intermittent and varies by
worker, since each pooled connection carries its own prepared-statement counter.

Unit tests cannot see any of this: they mock the repository, so no SQL is planned. The
loop below therefore runs the real statement on one real connection past the sixth
execution, which is the smallest thing that reproduces the bug.

Requires a Postgres reachable via ``settings.database.dsn`` with migrations applied.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.domain.models import SINGLETON_TOOL_TYPES
from contexts.agents.infrastructure import tables as t
from contexts.agents.infrastructure.repositories import AgentToolRepository
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

# Real Postgres required (see module docstring) -- routed to the backend-db CI job.
pytestmark = pytest.mark.db

# `sessionmaker` and `project_id` fixtures come from tests/integration/conftest.py.

# Postgres reports the index predicate back as `tool_type = ANY (ARRAY['x'::agent_tool_type, ...])`.
_INDEX_LITERAL = re.compile(r"'([a-z_]+)'::agent_tool_type")

# One past the fifth custom plan, which is where a parameterised predicate starts failing.
_EXECUTIONS = 8


@pytest.fixture
async def agent_id(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> AsyncIterator[uuid.UUID]:
    """A real agent row to hang agent_tools off. Torn down explicitly: agents holds an
    ON DELETE RESTRICT FK to key_groups, so it must not be left to the project cascade."""
    aid, kg_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(key_groups_t.insert().values(id=kg_id, project_id=project_id, name="itest-kg"))
        await session.execute(
            t.agents.insert().values(
                id=aid,
                project_id=project_id,
                name="itest-agent",
                model_hint="claude",
                key_group_id=kg_id,
            )
        )
        await session.commit()
    try:
        yield aid
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(t.agents.delete().where(t.agents.c.id == aid))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == kg_id))
            await cleanup.commit()


async def test_provision_singletons_survives_generic_plan(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_id: uuid.UUID,
) -> None:
    """Every agent create calls this. Repeated on one connection it must never raise
    InvalidColumnReferenceError, and it must stay idempotent at four rows."""
    async with sessionmaker() as session:
        repo = AgentToolRepository(session)
        for _ in range(_EXECUTIONS):
            await repo.provision_singletons(agent_id=agent_id)
        await session.commit()

        count = await session.scalar(
            sa.select(sa.func.count()).select_from(t.agent_tools).where(t.agent_tools.c.agent_id == agent_id)
        )
    assert count == len(SINGLETON_TOOL_TYPES)


async def test_index_predicate_matches_domain_singletons(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Anti-drift guard: the index predicate is frozen in migration 0036, while the code
    predicate is derived from SINGLETON_TOOL_TYPES. If the two ever diverge, ON CONFLICT
    inference stops matching -- so pin them to each other here rather than by convention.
    """
    async with sessionmaker() as session:
        indexdef = await session.scalar(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_agent_tools_singleton'")
        )
    assert indexdef is not None, "uq_agent_tools_singleton is missing; migration 0036 not applied"
    assert set(_INDEX_LITERAL.findall(indexdef)) == {tt.value for tt in SINGLETON_TOOL_TYPES}
