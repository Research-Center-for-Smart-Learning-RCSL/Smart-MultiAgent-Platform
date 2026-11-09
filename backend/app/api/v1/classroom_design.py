"""Classroom design runs ([R34.08]).

A project editor submits the design form, polls the run, and applies its
proposal to the teacher and peer agents. The run's agents must all belong to
the path's project (checked in the agents context) and the activity type must
be one the project may use. Apply takes no body: it writes at the versions the
run snapshotted, which are the prompts the panel shows as current.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, status
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.agents.interfaces.classroom_design_facade import (
    DESIGN_UNAVAILABLE,
    MAX_FORM_NOTES_CHARS,
    MAX_ROUNDS,
    MIN_ROUNDS,
    ClassroomDesignFacade,
    ClassroomDesignRun,
    DesignActivity,
    DesignForm,
    DesignRunStatus,
    Difficulty,
    PeerErrorPattern,
    Tone,
)
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal, require, scope_from_path
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue

router = APIRouter(prefix="/api/projects/{project_id}/classroom-design", tags=["classroom-design"])

_EDITOR = Depends(require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(project_param="project_id")))


class DesignRunIn(BaseModel):
    design_agent_id: uuid.UUID
    teacher_agent_id: uuid.UUID
    peer_agent_id: uuid.UUID
    activity_type_id: uuid.UUID
    difficulty: Literal["introductory", "intermediate", "advanced"]
    rounds: int = Field(ge=MIN_ROUNDS, le=MAX_ROUNDS)
    peer_error_pattern: Literal["none", "one_in_three"]
    tone: Literal["warm", "neutral", "concise"]
    notes: str = Field(default="", max_length=MAX_FORM_NOTES_CHARS)

    @field_validator("notes")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class DesignTargetOut(BaseModel):
    agent_id: uuid.UUID
    current_prompt: str
    current_version: int
    proposed_prompt: str | None


class DesignRunOut(BaseModel):
    id: uuid.UUID
    status: DesignRunStatus
    design_agent_id: uuid.UUID
    activity_type_id: uuid.UUID | None
    activity_name: str | None
    difficulty: str | None
    rounds: int | None
    peer_error_pattern: str | None
    tone: str | None
    form_notes: str
    teacher: DesignTargetOut
    peer: DesignTargetOut
    notes: str | None
    failure_reason: str | None
    created_at: datetime
    finished_at: datetime | None
    applied_at: datetime | None


class DesignApplyOut(BaseModel):
    run: DesignRunOut
    teacher_version: int
    peer_version: int


def _to_out(run: ClassroomDesignRun) -> DesignRunOut:
    params = run.params
    activity = params.get("activity") if isinstance(params.get("activity"), dict) else {}
    raw_type_id = activity.get("id") if activity else None
    rounds = params.get("rounds")
    return DesignRunOut(
        id=run.id,
        status=run.status,
        design_agent_id=run.design_agent_id,
        activity_type_id=uuid.UUID(raw_type_id) if isinstance(raw_type_id, str) else None,
        activity_name=activity.get("name") if activity else None,
        difficulty=params.get("difficulty"),
        rounds=rounds if isinstance(rounds, int) else None,
        peer_error_pattern=params.get("peer_error_pattern"),
        tone=params.get("tone"),
        form_notes=str(params.get("notes") or ""),
        teacher=DesignTargetOut(
            agent_id=run.teacher_agent_id,
            current_prompt=run.teacher_prompt_before,
            current_version=run.teacher_version_before,
            proposed_prompt=run.proposed_teacher_prompt,
        ),
        peer=DesignTargetOut(
            agent_id=run.peer_agent_id,
            current_prompt=run.peer_prompt_before,
            current_version=run.peer_version_before,
            proposed_prompt=run.proposed_peer_prompt,
        ),
        notes=run.notes,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        finished_at=run.finished_at,
        applied_at=run.applied_at,
    )


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_design_run(
    body: DesignRunIn,
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    _=_EDITOR,
    db: AsyncSession = Depends(db_session),
) -> DesignRunOut:
    """Queue a design run; 409 while another for this project is unfinished."""
    activity_type = await ActivitiesFacade(db).resolve_type_for_project(
        project_id=project_id, activity_type_id=body.activity_type_id
    )
    form = DesignForm(
        design_agent_id=body.design_agent_id,
        teacher_agent_id=body.teacher_agent_id,
        peer_agent_id=body.peer_agent_id,
        activity=DesignActivity(
            id=activity_type.id,
            key=activity_type.key,
            name=activity_type.name,
            payload_schema=dict(activity_type.payload_schema),
        ),
        difficulty=Difficulty(body.difficulty),
        rounds=body.rounds,
        peer_error_pattern=PeerErrorPattern(body.peer_error_pattern),
        tone=Tone(body.tone),
        notes=body.notes,
    )
    facade = ClassroomDesignFacade(db)
    run = await facade.create_run(project_id=project_id, form=form, actor_user_id=principal.user_id)
    # The job reads the row, so it must be committed first.
    await db.commit()
    try:
        await enqueue("run_classroom_design", str(run.id))
    except Exception as exc:
        # Otherwise the run would block the project until the stale threshold.
        logger.bind(run_id=str(run.id)).warning(f"design run enqueue failed: {type(exc).__name__}")
        await facade.record_failure(run.id, DESIGN_UNAVAILABLE)
        await db.commit()
        run = await facade.get_run(project_id=project_id, run_id=run.id)
    return _to_out(run)


@router.get("/runs")
async def list_design_runs(
    project_id: uuid.UUID = Path(...),
    limit: int = Query(10, ge=1, le=50),
    _=_EDITOR,
    db: AsyncSession = Depends(db_session),
) -> list[DesignRunOut]:
    """Newest first, so the panel can resume a run after a reload."""
    runs = await ClassroomDesignFacade(db).list_runs(project_id, limit=limit)
    return [_to_out(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_design_run(
    project_id: uuid.UUID = Path(...),
    run_id: uuid.UUID = Path(...),
    _=_EDITOR,
    db: AsyncSession = Depends(db_session),
) -> DesignRunOut:
    return _to_out(await ClassroomDesignFacade(db).get_run(project_id=project_id, run_id=run_id))


@router.post("/runs/{run_id}/apply")
async def apply_design_run(
    project_id: uuid.UUID = Path(...),
    run_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=_EDITOR,
    db: AsyncSession = Depends(db_session),
) -> DesignApplyOut:
    """Write both proposed prompts in this request's transaction, or neither.

    409 ``agents/design-apply-conflict`` when either agent changed since the
    run was drafted. The session rolls back, so nothing is written; the panel
    asks for a new run, which drafts from the current prompts.
    """
    facade = ClassroomDesignFacade(db)
    teacher, peer = await facade.apply(
        project_id=project_id,
        run_id=run_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    run = await facade.get_run(project_id=project_id, run_id=run_id)
    return DesignApplyOut(run=_to_out(run), teacher_version=teacher.version, peer_version=peer.version)


__all__ = ["router"]
