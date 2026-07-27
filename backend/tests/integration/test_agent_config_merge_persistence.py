"""`wakeup_config` keys must survive a partial write through real JSONB.

The unit tests for this behaviour assert the `values` dict `AgentService.patch`
hands the repository — they mock the repository, so nothing is ever serialised.
That leaves the question the defect was actually about unanswered: does the
merged dict *persist* and read back intact, including the designer keys the
editor never sends?

Requires a Postgres reachable via ``settings.database.dsn`` with migrations applied.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.models import AgentDraft
from contexts.agents.infrastructure import tables as t
from contexts.identity.infrastructure.tables import users as users_t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db

# `sessionmaker` and `project` fixtures come from tests/integration/conftest.py.
#
# Actors are fixed ids seeded once and deliberately never torn down. `patch` emits
# an `agent.edited` audit row, `audit_logs.actor_user_id` carries an FK to `users`,
# and `audit_logs` is append-only at the database level (R17.04) — so the row cannot
# be deleted and its actor must outlive the test. That also rules out the conftest
# `project` fixture's owner, whose teardown deletes the user.
_SYSTEM_ACTOR = uuid.UUID(int=0)
_HUMAN_ACTOR = uuid.UUID("00000000-0000-4000-8000-00000000a11e")

# What the editor sends: the full shape it models, with no `soft_bounds` — it has
# no control for it (R15.08 is admin-set through the API).
_EDITOR_PAYLOAD = {
    "triggers": {
        "every_n_messages": {"enabled": True, "n": 8},
        "silence_minutes": {"enabled": False, "t_minutes": 30, "autostop_rounds": 5},
        "call_only": {"enabled": False},
    },
    "allow_self_open": False,
    "refresh_every_hours": 24,
}

_AUTHORED = {
    "triggers": {"every_n_messages": {"enabled": True, "n": 3}},
    "soft_bounds": {"n_min": 5, "n_max": 10},
    "designer_note": "keep me",
}


async def _ensure_actor(
    sessionmaker: async_sessionmaker[AsyncSession],
    actor_id: uuid.UUID,
    email: str,
) -> uuid.UUID:
    """Seed an audit-capable actor if absent. Not torn down — see the module note:
    the `agent.edited` row it owns is undeletable, so the user must stay."""
    async with sessionmaker() as session:
        existing = await session.scalar(
            sa.select(sa.func.count()).select_from(users_t).where(users_t.c.id == actor_id)
        )
        if not existing:
            await session.execute(
                users_t.insert().values(
                    id=actor_id,
                    email=email,
                    password_hash="x",  # never authenticated against
                )
            )
            await session.commit()
    return actor_id


@pytest.fixture
async def system_actor(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """`uuid(int=0)` is the actor the wake-up service writes as (G.4/G.5). Seeded here
    rather than assumed, so the test does not depend on how the database was bootstrapped."""
    return await _ensure_actor(sessionmaker, _SYSTEM_ACTOR, "itest-system@test.invalid")


@pytest.fixture
async def human_actor(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """A non-system actor, standing in for the project member editing the agent."""
    return await _ensure_actor(sessionmaker, _HUMAN_ACTOR, "itest-human@test.invalid")


@pytest.fixture
async def agent_ids(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """A real agent row carrying designer bounds. Torn down explicitly: agents holds an
    ON DELETE RESTRICT FK to key_groups, so it must not be left to the project cascade."""
    project_id, _ = project
    aid, kg_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(key_groups_t.insert().values(id=kg_id, project_id=project_id, name="itest-kg"))
        await session.execute(
            t.agents.insert().values(
                id=aid,
                project_id=project_id,
                name="itest-merge-agent",
                model_hint="claude",
                key_group_id=kg_id,
                wakeup_config=_AUTHORED,
                wakeup_authored_snapshot=_AUTHORED,
            )
        )
        await session.commit()
    try:
        yield aid, kg_id
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(t.agents.delete().where(t.agents.c.id == aid))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == kg_id))
            await cleanup.commit()


async def _read(session: AsyncSession, agent_id: uuid.UUID) -> sa.Row:
    return (
        await session.execute(
            sa.select(
                t.agents.c.wakeup_config,
                t.agents.c.wakeup_authored_snapshot,
                t.agents.c.version,
            ).where(t.agents.c.id == agent_id)
        )
    ).one()


async def test_editor_shaped_patch_persists_designer_keys(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_ids: tuple[uuid.UUID, uuid.UUID],
    human_actor: uuid.UUID,
) -> None:
    """The reproduction from the dossier, end to end against real JSONB: a human edit
    that omits `soft_bounds` must not erase it — from either column."""
    agent_id, _ = agent_ids

    async with sessionmaker() as session:
        before = await _read(session, agent_id)
        await AgentService(session).patch(
            agent_id=agent_id,
            draft=AgentDraft(wakeup_config=_EDITOR_PAYLOAD),
            expected_version=before.version,
            actor_user_id=human_actor,
            actor_ip=None,
        )
        await session.commit()

    async with sessionmaker() as session:
        after = await _read(session, agent_id)

    assert after.wakeup_config["soft_bounds"] == {"n_min": 5, "n_max": 10}
    assert after.wakeup_config["designer_note"] == "keep me"
    assert after.wakeup_config["triggers"]["every_n_messages"]["n"] == 8
    # A human edit re-authors the baseline, and it must be the merged value:
    # storing the fragment is what made the loss unrecoverable.
    assert after.wakeup_authored_snapshot == after.wakeup_config


async def test_explicit_null_deletes_through_jsonb(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_ids: tuple[uuid.UUID, uuid.UUID],
    human_actor: uuid.UUID,
) -> None:
    agent_id, _ = agent_ids

    async with sessionmaker() as session:
        before = await _read(session, agent_id)
        await AgentService(session).patch(
            agent_id=agent_id,
            draft=AgentDraft(wakeup_config={"soft_bounds": None}),
            expected_version=before.version,
            actor_user_id=human_actor,
            actor_ip=None,
        )
        await session.commit()

    async with sessionmaker() as session:
        after = await _read(session, agent_id)

    assert "soft_bounds" not in after.wakeup_config
    assert after.wakeup_config["designer_note"] == "keep me"


async def test_refresh_restores_the_snapshot_and_then_converges(
    sessionmaker: async_sessionmaker[AsyncSession],
    agent_ids: tuple[uuid.UUID, uuid.UUID],
    system_actor: uuid.UUID,
) -> None:
    """G.5 is a restore, not a patch. After it runs, `wakeup_config` must equal
    `wakeup_authored_snapshot` exactly — otherwise the drift check never settles and
    every later sweep refreshes and audits the same agent forever."""
    agent_id, _ = agent_ids

    # Drift the live config the way a self-modification does: a full normalised
    # dict carrying keys the partial authored snapshot never had.
    async with sessionmaker() as session:
        before = await _read(session, agent_id)
        await AgentService(session).patch(
            agent_id=agent_id,
            draft=AgentDraft(wakeup_config=_EDITOR_PAYLOAD),
            expected_version=before.version,
            actor_user_id=system_actor,  # self-modification: snapshot untouched
            actor_ip=None,
        )
        await session.commit()

    async with sessionmaker() as session:
        drifted = await _read(session, agent_id)
        assert drifted.wakeup_config != drifted.wakeup_authored_snapshot
        assert drifted.wakeup_authored_snapshot == _AUTHORED

        await AgentService(session).patch(
            agent_id=agent_id,
            draft=AgentDraft(
                wakeup_config=drifted.wakeup_authored_snapshot,
                replace_wakeup_config=True,
            ),
            expected_version=drifted.version,
            actor_user_id=system_actor,
            actor_ip=None,
        )
        await session.commit()

    async with sessionmaker() as session:
        restored = await _read(session, agent_id)

    assert restored.wakeup_config == _AUTHORED
    assert restored.wakeup_config == restored.wakeup_authored_snapshot
