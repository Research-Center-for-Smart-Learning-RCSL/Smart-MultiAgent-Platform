"""Prompt revisions and classroom design runs against a real PostgreSQL (AC-17, AC-19).

What only a real engine shows: the version trigger the revision order relies
on, the one-in-flight partial unique index, the CHECK constraints, and that a
conflicting apply leaves both agents as they were once the transaction rolls
back.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.application.agent_service import AgentService
from contexts.agents.application.classroom_design_service import ClassroomDesignService
from contexts.agents.domain.classroom_design import (
    DesignActivity,
    DesignForm,
    DesignProposal,
    DesignRunStatus,
    Difficulty,
    PeerErrorPattern,
    PromptRevisionSource,
    Tone,
)
from contexts.agents.domain.errors import (
    DesignApplyConflict,
    DesignRunInFlight,
    DesignRunNotApplicable,
)
from contexts.agents.domain.models import AgentDraft
from contexts.agents.infrastructure import tables as agt
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Agents:
    project_id: uuid.UUID
    user_id: uuid.UUID
    design: uuid.UUID
    teacher: uuid.UUID
    peer: uuid.UUID


@pytest.fixture
async def agents(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Agents]:
    project_id, user_id = project
    key_group_id = uuid.uuid4()
    ids = [uuid.uuid4() for _ in range(3)]
    async with sessionmaker() as session:
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="design-itest-kg")
        )
        for n, agent_id in enumerate(ids):
            await session.execute(
                agt.agents.insert().values(
                    id=agent_id,
                    project_id=project_id,
                    name=f"design-itest-{n}",
                    model_hint="claude",
                    key_group_id=key_group_id,
                    system_prompt=f"prompt {n}",
                )
            )
        await session.commit()
    try:
        yield Agents(project_id, user_id, *ids)
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(
                agt.classroom_design_runs.delete().where(agt.classroom_design_runs.c.project_id == project_id)
            )
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id.in_(ids)))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


async def _version(session: AsyncSession, agent_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(sa.select(agt.agents.c.version).where(agt.agents.c.id == agent_id))
        ).scalar_one()
    )


async def _prompt(session: AsyncSession, agent_id: uuid.UUID) -> str:
    return str(
        (
            await session.execute(sa.select(agt.agents.c.system_prompt).where(agt.agents.c.id == agent_id))
        ).scalar_one()
    )


async def _set_prompt(session: AsyncSession, a: Agents, agent_id: uuid.UUID, prompt: str) -> None:
    await AgentService(session).patch(
        agent_id=agent_id,
        draft=AgentDraft(system_prompt=prompt),
        expected_version=await _version(session, agent_id),
        actor_user_id=a.user_id,
        actor_ip=None,
    )
    await session.commit()


def _form(a: Agents) -> DesignForm:
    return DesignForm(
        design_agent_id=a.design,
        teacher_agent_id=a.teacher,
        peer_agent_id=a.peer,
        activity=DesignActivity(id=uuid.uuid4(), key="crat", name="Word puzzle", payload_schema={}),
        difficulty=Difficulty.INTERMEDIATE,
        rounds=3,
        peer_error_pattern=PeerErrorPattern.ONE_IN_THREE,
        tone=Tone.WARM,
        notes="",
    )


async def _succeeded_run(session: AsyncSession, a: Agents) -> uuid.UUID:
    svc = ClassroomDesignService(session)
    run = await svc.create_run(project_id=a.project_id, form=_form(a), actor_user_id=a.user_id)
    await session.commit()
    assert await svc.begin(run.id) is not None
    assert await svc.record_success(
        run.id, DesignProposal(teacher_system_prompt="T designed", peer_system_prompt="P designed", notes="n")
    )
    await session.commit()
    return run.id


class TestRevisions:
    async def test_changes_are_recorded_newest_first_with_a_baseline(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        """AC-17."""
        async with sessionmaker() as session:
            v0 = await _version(session, agents.teacher)
            await _set_prompt(session, agents, agents.teacher, "second")
            await _set_prompt(session, agents, agents.teacher, "third")
            # A patch that does not touch the prompt records nothing.
            await AgentService(session).patch(
                agent_id=agents.teacher,
                draft=AgentDraft(name="design-itest-renamed"),
                expected_version=await _version(session, agents.teacher),
                actor_user_id=agents.user_id,
                actor_ip=None,
            )
            await session.commit()

            rows = await AgentService(session).list_prompt_revisions(agents.teacher, limit=50, offset=0)

        assert [(r.source, r.system_prompt) for r in rows] == [
            (PromptRevisionSource.MANUAL, "third"),
            (PromptRevisionSource.MANUAL, "second"),
            (PromptRevisionSource.INITIAL, "prompt 1"),
        ]
        assert rows[-1].agent_version == v0
        assert rows[0].agent_version > rows[1].agent_version > v0
        assert rows[0].actor_user_id == agents.user_id

    async def test_a_restore_sets_the_prompt_back_and_is_itself_a_revision(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        """AC-17."""
        async with sessionmaker() as session:
            await _set_prompt(session, agents, agents.peer, "changed")
            baseline = (await AgentService(session).list_prompt_revisions(agents.peer, limit=50, offset=0))[
                -1
            ]
            await AgentService(session).restore_prompt_revision(
                agent_id=agents.peer,
                revision_id=baseline.id,
                expected_version=await _version(session, agents.peer),
                actor_user_id=agents.user_id,
                actor_ip=None,
            )
            await session.commit()

            assert await _prompt(session, agents.peer) == "prompt 2"
            newest = (await AgentService(session).list_prompt_revisions(agents.peer, limit=1, offset=0))[0]

        assert (newest.source, newest.restored_from_id, newest.system_prompt) == (
            PromptRevisionSource.RESTORE,
            baseline.id,
            "prompt 2",
        )

    async def test_an_unknown_source_is_refused_by_the_database(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        async with sessionmaker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    agt.agent_prompt_revisions.insert().values(
                        agent_id=agents.teacher, agent_version=999, system_prompt="x", source="llm"
                    )
                )


class TestRuns:
    async def test_one_run_in_flight_per_project(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        async with sessionmaker() as session:
            svc = ClassroomDesignService(session)
            await svc.create_run(
                project_id=agents.project_id, form=_form(agents), actor_user_id=agents.user_id
            )
            await session.commit()
            with pytest.raises(DesignRunInFlight):
                await svc.create_run(
                    project_id=agents.project_id, form=_form(agents), actor_user_id=agents.user_id
                )
            # The savepoint kept the session usable after the refusal.
            assert len(await svc.list_runs(agents.project_id, limit=10)) == 1

    async def test_a_stale_run_no_longer_blocks_a_new_one(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        async with sessionmaker() as session:
            svc = ClassroomDesignService(session)
            stale = await svc.create_run(
                project_id=agents.project_id, form=_form(agents), actor_user_id=agents.user_id
            )
            await session.execute(
                agt.classroom_design_runs.update()
                .where(agt.classroom_design_runs.c.id == stale.id)
                .values(created_at=sa.func.now() - sa.text("interval '1 hour'"))
            )
            await session.commit()

            fresh = await svc.create_run(
                project_id=agents.project_id, form=_form(agents), actor_user_id=agents.user_id
            )
            await session.commit()
            old = await svc.get_run(project_id=agents.project_id, run_id=stale.id)

        assert (old.status, old.failure_reason) == (DesignRunStatus.FAILED, "design_stale")
        assert fresh.status is DesignRunStatus.PENDING

    async def test_a_succeeded_run_needs_both_prompts(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        async with sessionmaker() as session:
            run = await ClassroomDesignService(session).create_run(
                project_id=agents.project_id, form=_form(agents), actor_user_id=agents.user_id
            )
            with pytest.raises(IntegrityError):
                await session.execute(
                    agt.classroom_design_runs.update()
                    .where(agt.classroom_design_runs.c.id == run.id)
                    .values(status="succeeded")
                )


class TestApply:
    async def test_apply_writes_both_agents_with_design_revisions_once(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        """AC-19."""
        async with sessionmaker() as session:
            run_id = await _succeeded_run(session, agents)
            svc = ClassroomDesignService(session)
            await svc.apply(
                project_id=agents.project_id, run_id=run_id, actor_user_id=agents.user_id, actor_ip=None
            )
            await session.commit()

            assert await _prompt(session, agents.teacher) == "T designed"
            assert await _prompt(session, agents.peer) == "P designed"
            for agent_id in (agents.teacher, agents.peer):
                newest = (await AgentService(session).list_prompt_revisions(agent_id, limit=1, offset=0))[0]
                assert (newest.source, newest.design_run_id) == (PromptRevisionSource.DESIGN, run_id)
            run = await svc.get_run(project_id=agents.project_id, run_id=run_id)
            assert run.status is DesignRunStatus.APPLIED
            assert run.applied_by_user_id == agents.user_id

            with pytest.raises(DesignRunNotApplicable):
                await svc.apply(
                    project_id=agents.project_id, run_id=run_id, actor_user_id=agents.user_id, actor_ip=None
                )

    async def test_a_changed_agent_rolls_back_both(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        """AC-19: the peer changed after the run, so the teacher is not written either."""
        async with sessionmaker() as session:
            run_id = await _succeeded_run(session, agents)
            await _set_prompt(session, agents, agents.peer, "edited by hand")
            teacher_version = await _version(session, agents.teacher)

            with pytest.raises(DesignApplyConflict):
                await ClassroomDesignService(session).apply(
                    project_id=agents.project_id, run_id=run_id, actor_user_id=agents.user_id, actor_ip=None
                )
            await session.rollback()

            assert await _prompt(session, agents.teacher) == "prompt 1"
            assert await _version(session, agents.teacher) == teacher_version
            assert await _prompt(session, agents.peer) == "edited by hand"
            run = await ClassroomDesignService(session).get_run(project_id=agents.project_id, run_id=run_id)
            assert run.status is DesignRunStatus.SUCCEEDED

    async def test_two_concurrent_applies_write_once(
        self, sessionmaker: async_sessionmaker[AsyncSession], agents: Agents
    ) -> None:
        """AC-19: a run cannot be applied twice."""
        async with sessionmaker() as session:
            run_id = await _succeeded_run(session, agents)

        async def apply_once() -> str:
            async with sessionmaker() as session:
                try:
                    await ClassroomDesignService(session).apply(
                        project_id=agents.project_id,
                        run_id=run_id,
                        actor_user_id=agents.user_id,
                        actor_ip=None,
                    )
                except DesignRunNotApplicable:
                    await session.rollback()
                    return "refused"
                await session.commit()
                return "applied"

        outcomes = sorted(await asyncio.gather(apply_once(), apply_once()))

        assert outcomes == ["applied", "refused"]
        async with sessionmaker() as session:
            rows = await AgentService(session).list_prompt_revisions(agents.teacher, limit=50, offset=0)
        assert [r.source for r in rows].count(PromptRevisionSource.DESIGN) == 1
