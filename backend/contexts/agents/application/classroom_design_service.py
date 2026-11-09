"""Classroom design runs ([R34.08]): submit, track, apply.

The design agent's provider call happens in the ``run_classroom_design`` job;
this service owns the run row and the apply. Apply writes both target agents
through ``AgentService.patch`` in the caller's transaction, at the versions the
run snapshotted, so either both prompts land or neither does. Never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.classroom_design import (
    STALE_RUN_SECONDS,
    ClassroomDesignRun,
    DesignForm,
    DesignProposal,
    DesignRunStatus,
    PromptRevisionSource,
)
from contexts.agents.domain.errors import (
    AgentVersionMismatch,
    DesignAgentOutOfProject,
    DesignApplyConflict,
    DesignRunNotApplicable,
    DesignRunNotFound,
    DesignTargetsInvalid,
)
from contexts.agents.domain.models import Agent, AgentDraft
from contexts.agents.infrastructure.classroom_design_repositories import DesignRunRepository
from contexts.agents.infrastructure.repositories import AgentRepository
from shared_kernel import audit


class ClassroomDesignService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._runs = DesignRunRepository(db)
        self._agents = AgentRepository(db)

    async def _project_agent(self, agent_id: uuid.UUID, project_id: uuid.UUID) -> Agent:
        agent = await self._agents.get(agent_id)
        if agent is None or agent.project_id != project_id:
            raise DesignAgentOutOfProject(str(agent_id))
        return agent

    async def create_run(
        self, *, project_id: uuid.UUID, form: DesignForm, actor_user_id: uuid.UUID
    ) -> ClassroomDesignRun:
        """A pending run, or ``DesignRunInFlight`` while another is unfinished.

        The caller enqueues ``run_classroom_design`` after committing.
        """
        if form.teacher_agent_id == form.peer_agent_id:
            raise DesignTargetsInvalid("teacher and peer are the same agent")
        await self._project_agent(form.design_agent_id, project_id)
        teacher = await self._project_agent(form.teacher_agent_id, project_id)
        peer = await self._project_agent(form.peer_agent_id, project_id)
        # A job that never ran (worker down, enqueue lost) must not block the
        # project for good; the threshold is well past the job timeout.
        await self._runs.expire_stale(project_id=project_id, older_than_seconds=STALE_RUN_SECONDS)
        return await self._runs.create(
            project_id=project_id,
            requested_by_user_id=actor_user_id,
            design_agent_id=form.design_agent_id,
            teacher_agent_id=teacher.id,
            peer_agent_id=peer.id,
            params=form.params(),
            teacher_prompt_before=teacher.system_prompt,
            teacher_version_before=teacher.version,
            peer_prompt_before=peer.system_prompt,
            peer_version_before=peer.version,
        )

    async def get_run(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> ClassroomDesignRun:
        run = await self._runs.get(run_id)
        if run is None or run.project_id != project_id:
            raise DesignRunNotFound(str(run_id))
        return run

    async def list_runs(self, project_id: uuid.UUID, *, limit: int) -> Sequence[ClassroomDesignRun]:
        return await self._runs.list_for_project(project_id, limit=limit)

    async def apply(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[Agent, Agent]:
        """Write both proposed prompts, or nothing.

        Raises ``DesignApplyConflict`` when either agent's version moved since the
        run snapshotted it, ``DesignRunNotApplicable`` when the run has no
        proposal or was applied already. On either error the caller must roll
        back: a conflict on the second agent comes after the first was written.
        """
        run = await self.get_run(project_id=project_id, run_id=run_id)
        if run.status is not DesignRunStatus.SUCCEEDED:
            raise DesignRunNotApplicable(str(run_id))
        teacher_prompt, peer_prompt = run.proposed_teacher_prompt, run.proposed_peer_prompt
        if teacher_prompt is None or peer_prompt is None:
            raise DesignRunNotApplicable(str(run_id))
        # Compare-and-set first: of two concurrent applies, the loser stops here
        # before touching an agent, rather than on the agents' version check.
        if not await self._runs.mark_applied(run_id, applied_by_user_id=actor_user_id):
            raise DesignRunNotApplicable(str(run_id))
        service = AgentService(self._db)
        written: list[Agent] = []
        for agent_id, version, prompt in (
            (run.teacher_agent_id, run.teacher_version_before, teacher_prompt),
            (run.peer_agent_id, run.peer_version_before, peer_prompt),
        ):
            # Same project guard as at submit: the agents table is the authority,
            # and a target soft-deleted since then reads as missing.
            await self._project_agent(agent_id, project_id)
            try:
                written.append(
                    await service.patch(
                        agent_id=agent_id,
                        draft=AgentDraft(system_prompt=prompt),
                        expected_version=version,
                        actor_user_id=actor_user_id,
                        actor_ip=actor_ip,
                        request_id=request_id,
                        prompt_source=PromptRevisionSource.DESIGN,
                        design_run_id=run.id,
                    )
                )
            except AgentVersionMismatch as exc:
                raise DesignApplyConflict(str(agent_id)) from exc
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="classroom_design.applied",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="classroom_design_run",
                resource_id=run.id,
                metadata={
                    "project_id": str(project_id),
                    "teacher_agent_id": str(run.teacher_agent_id),
                    "peer_agent_id": str(run.peer_agent_id),
                },
                request_id=request_id,
            ),
        )
        return written[0], written[1]

    # -- Worker surface ----------------------------------------------------

    async def begin(self, run_id: uuid.UUID) -> ClassroomDesignRun | None:
        """The run, moved to ``running``; ``None`` if it was not pending."""
        if not await self._runs.mark_running(run_id):
            return None
        return await self._runs.get(run_id)

    async def record_success(self, run_id: uuid.UUID, proposal: DesignProposal) -> bool:
        return await self._runs.record_success(run_id, proposal)

    async def record_failure(self, run_id: uuid.UUID, reason: str) -> bool:
        return await self._runs.record_failure(run_id, reason)


__all__ = ["ClassroomDesignService"]
