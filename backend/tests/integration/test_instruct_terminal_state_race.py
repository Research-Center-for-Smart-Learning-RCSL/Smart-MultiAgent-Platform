"""F-15 — the instruct terminal-state guard under real concurrent writers (spec
§4, §8, AC-1/AC-2).

Every unit test of ``InstructService`` fakes ``InstructionRepository``, so none
of them can prove the property the fix rests on: that two sessions racing a
terminal write serialize through the CAS's ``WHERE`` clause rather than one
silently clobbering the other under READ COMMITTED. The interleaving in
``test_concurrent_deadline_and_completion_leaves_one_terminal_state`` matches
the spec's §4 reproduction exactly.

Requires a Postgres reachable via ``settings.database.dsn`` with migrations
applied -- the ``backend-integration`` CI job's environment.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agents_t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t
from contexts.orchestration.application.instruct_service import InstructService
from contexts.orchestration.domain.models import InstructionState
from contexts.orchestration.infrastructure.tables import instructions as instructions_t

# Real Postgres required (see module docstring) -- routed to the backend-db CI job.
pytestmark = pytest.mark.db

# `sessionmaker` and `project_id` fixtures come from tests/integration/conftest.py.


@pytest.fixture
async def agent_pair(
    sessionmaker: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two real agent rows (issuer, target) to hang instructions off. Torn down
    explicitly: agents holds an ON DELETE RESTRICT FK to key_groups."""
    issuer_id, target_id, kg_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            key_groups_t.insert().values(id=kg_id, project_id=project_id, name="itest-kg"),
        )
        await session.execute(
            agents_t.agents.insert(),
            [
                {
                    "id": issuer_id,
                    "project_id": project_id,
                    "name": "itest-issuer",
                    "model_hint": "claude",
                    "key_group_id": kg_id,
                },
                {
                    "id": target_id,
                    "project_id": project_id,
                    "name": "itest-target",
                    "model_hint": "claude",
                    "key_group_id": kg_id,
                },
            ],
        )
        await session.commit()
    try:
        yield issuer_id, target_id
    finally:
        async with sessionmaker() as cleanup:
            # Instructions seeded by tests FK-reference these agents (RESTRICT);
            # drop them first or the agent delete below violates the constraint.
            await cleanup.execute(
                instructions_t.delete().where(
                    sa.or_(
                        instructions_t.c.issuer_agent_id.in_([issuer_id, target_id]),
                        instructions_t.c.target_agent_id.in_([issuer_id, target_id]),
                    ),
                ),
            )
            await cleanup.execute(
                agents_t.agents.delete().where(agents_t.agents.c.id.in_([issuer_id, target_id])),
            )
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == kg_id))
            await cleanup.commit()


async def _seed_instruction(
    session: AsyncSession,
    *,
    issuer_id: uuid.UUID,
    target_id: uuid.UUID,
) -> uuid.UUID:
    iid = uuid.uuid4()
    await session.execute(
        instructions_t.insert().values(
            id=iid,
            chain_id=uuid.uuid4(),
            path=[issuer_id],
            depth=0,
            issuer_agent_id=issuer_id,
            target_agent_id=target_id,
            payload={},
            state=InstructionState.ISSUED.value,
        ),
    )
    await session.commit()
    return iid


async def _read_row(session: AsyncSession, iid: uuid.UUID) -> sa.Row:
    row = (
        await session.execute(
            sa.select(instructions_t.c.state, instructions_t.c.resolved_at).where(
                instructions_t.c.id == iid,
            ),
        )
    ).first()
    assert row is not None
    return row


async def test_timeout_does_not_overwrite_completed(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_pair: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """T-1 (AC-1), the primary failing test. Session A completes and commits
    first; session B's later timeout must be rejected, not silently applied."""
    issuer_id, target_id = agent_pair
    async with sessionmaker() as setup:
        iid = await _seed_instruction(setup, issuer_id=issuer_id, target_id=target_id)

    async with sessionmaker() as session_a:
        won_a = await InstructService(session_a).mark_completed(iid)
        await session_a.commit()
    assert won_a is True

    async with sessionmaker() as session_b:
        won_b = await InstructService(session_b).mark_timeout(iid)
        await session_b.commit()
    assert won_b is False

    async with sessionmaker() as check:
        row = await _read_row(check, iid)
    assert row.state == InstructionState.COMPLETED.value
    assert row.resolved_at is not None


async def test_concurrent_deadline_and_completion_leaves_one_terminal_state(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_pair: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """T-2 (AC-2), the exact F-15 interleaving from spec §4: session B reads and
    observes not-yet-terminal (mirroring the deadline worker's application-level
    guard), session A then commits COMPLETED, and only then does B's CAS write
    execute -- it must be rejected by the WHERE predicate, not clobber A."""
    issuer_id, target_id = agent_pair
    async with sessionmaker() as setup:
        iid = await _seed_instruction(setup, issuer_id=issuer_id, target_id=target_id)

    b_read = asyncio.Event()
    a_committed = asyncio.Event()

    async def session_a() -> None:
        await b_read.wait()
        async with sessionmaker() as session:
            won = await InstructService(session).mark_completed(iid)
            await session.commit()
        assert won is True
        a_committed.set()

    results: dict[str, bool] = {}

    async def session_b() -> None:
        async with sessionmaker() as session:
            instruction = await InstructService(session).get_instruction(iid)
            assert instruction is not None
            assert instruction.state is InstructionState.ISSUED
            b_read.set()
            await a_committed.wait()
            results["won_b"] = await InstructService(session).mark_timeout(iid)
            await session.commit()

    await asyncio.gather(session_a(), session_b())

    assert results["won_b"] is False
    async with sessionmaker() as check:
        row = await _read_row(check, iid)
    assert row.state == InstructionState.COMPLETED.value


async def test_completed_does_not_overwrite_timeout(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_pair: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """T-3, pinning Q-2: the inverse race. Once TIMEOUT commits, a late
    completion is rejected -- the deadline wins, not last-writer-wins."""
    issuer_id, target_id = agent_pair
    async with sessionmaker() as setup:
        iid = await _seed_instruction(setup, issuer_id=issuer_id, target_id=target_id)

    async with sessionmaker() as session_b:
        won_b = await InstructService(session_b).mark_timeout(iid)
        await session_b.commit()
    assert won_b is True

    async with sessionmaker() as session_a:
        won_a = await InstructService(session_a).mark_completed(iid)
        await session_a.commit()
    assert won_a is False

    async with sessionmaker() as check:
        row = await _read_row(check, iid)
    assert row.state == InstructionState.TIMEOUT.value


async def test_delivered_does_not_revive_a_settled_instruction(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_pair: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """T-4, the third instance (spec §2): a short ``completion_timeout_seconds``
    deadline fires while the envelope is still queued; the consumer's later
    DELIVERED must not revive a settled TIMEOUT row."""
    issuer_id, target_id = agent_pair
    async with sessionmaker() as setup:
        iid = await _seed_instruction(setup, issuer_id=issuer_id, target_id=target_id)

    async with sessionmaker() as session:
        await InstructService(session).mark_timeout(iid)
        await session.commit()

    async with sessionmaker() as session:
        won = await InstructService(session).mark_delivered(iid)
        await session.commit()
    assert won is False

    async with sessionmaker() as check:
        row = await _read_row(check, iid)
    assert row.state == InstructionState.TIMEOUT.value


async def test_update_state_on_absent_row_still_raises(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """T-5, pinning the Q-3 contract: an absent row is a distinct outcome from a
    rejected transition -- ValueError, not a returned False -- so the CAS does
    not degrade the pre-existing not-found contract."""
    async with sessionmaker() as session:
        with pytest.raises(ValueError):
            await InstructService(session).mark_completed(uuid.uuid4())
