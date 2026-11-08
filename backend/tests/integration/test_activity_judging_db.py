"""The judge state a submission records and the queries the judge job runs.

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Creativity scoring), D-18.

A db-tier test because the queries are PostgreSQL: the per-session cap counts
rows under the session lock, the job reads the definition through an outer join
to the revision it was submitted under, and both write-backs are guarded on
``pending`` in SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.creativity_judge import (
    JUDGE_TIMEOUT,
    MAX_JUDGED_ATTEMPTS_PER_SESSION,
    SKIP_JUDGE_CAP,
    JudgeStatus,
)
from contexts.activities.domain.models import ValidatorKind
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.infrastructure import tables as ct

pytestmark = pytest.mark.db

_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"answer": {"type": "string"}}}
_VALIDATOR: dict[str, Any] = {"validator_id": "filled_count", "min_filled": 1}
_RUBRIC_V1 = {"flexibility": "f1", "originality": "o1", "convergence": "c1"}
_RUBRIC_V2 = {"flexibility": "f2", "originality": "o2", "convergence": "c2"}


async def _scored_room(
    sessionmaker: async_sessionmaker[AsyncSession], project_id: uuid.UUID, owner_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, dict[str, Any]]:
    scoring = {"judge_agent_id": str(uuid.uuid4()), "rubric": _RUBRIC_V1}
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        registration = await ActivitiesFacade(session).register_type(
            project_id=project_id,
            key=f"judging-itest-{uuid.uuid4().hex[:8]}",
            name="judging itest",
            payload_schema=_SCHEMA,
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config=_VALIDATOR,
            retention_days=None,
            actor_user_id=owner_id,
            actor_ip=None,
            creativity_scoring=scoring,
        )
        type_id = registration.activity_type.id
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="judging-itest")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="judging-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=owner_id,
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                chatroom_id=chatroom_id, activity_type_id=type_id, started_by_user_id=owner_id
            )
        )
        await session.commit()
    return type_id, chatroom_id, scoring


async def _submit(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    type_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    answer: str,
) -> uuid.UUID:
    async with sessionmaker() as session:
        submission, _signal = await ActivitiesFacade(session).submit(
            project_id=project_id,
            activity_type_id=type_id,
            chatroom_id=chatroom_id,
            producer_user_id=owner_id,
            subject_user_id=owner_id,
            caller_user_id=None,
            payload={"answer": answer},
            actor_user_id=owner_id,
            actor_ip=None,
        )
        await session.commit()
    return submission.id


async def _judge_state(session: AsyncSession, submission_id: uuid.UUID) -> tuple[str | None, str | None]:
    row = (
        await session.execute(
            sa.select(at.activity_submissions.c.judge_status, at.activity_submissions.c.judge_reason).where(
                at.activity_submissions.c.id == submission_id
            )
        )
    ).one()
    return row.judge_status, row.judge_reason


async def test_attempts_past_the_cap_are_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    type_id, chatroom_id, _scoring = await _scored_room(sessionmaker, project_id, owner_id)

    ids = [
        await _submit(
            sessionmaker,
            project_id=project_id,
            owner_id=owner_id,
            type_id=type_id,
            chatroom_id=chatroom_id,
            answer=f"try {n}",
        )
        for n in range(MAX_JUDGED_ATTEMPTS_PER_SESSION + 1)
    ]

    async with sessionmaker() as session:
        states = [await _judge_state(session, i) for i in ids]
    assert states[:-1] == [(JudgeStatus.PENDING.value, None)] * MAX_JUDGED_ATTEMPTS_PER_SESSION
    assert states[-1] == (JudgeStatus.SKIPPED.value, SKIP_JUDGE_CAP)


async def test_the_job_reads_the_definition_the_row_was_submitted_under(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    type_id, chatroom_id, scoring = await _scored_room(sessionmaker, project_id, owner_id)
    submission_id = await _submit(
        sessionmaker,
        project_id=project_id,
        owner_id=owner_id,
        type_id=type_id,
        chatroom_id=chatroom_id,
        answer="umbrella",
    )
    # The rubric changes after the answer was given.
    async with sessionmaker() as session:
        await ActivityTypeRepository(session).update(
            type_id,
            name="judging itest",
            payload_schema=_SCHEMA,
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config=_VALIDATOR,
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
            bump_version=True,
            creativity_scoring={**scoring, "rubric": _RUBRIC_V2},
        )
        await session.commit()

    async with sessionmaker() as session:
        item = await ActivitiesFacade(session).get_submission_for_judging(submission_id)
    assert item is not None
    assert item.judge_status == JudgeStatus.PENDING.value
    assert (item.project_id, item.chatroom_id) == (project_id, chatroom_id)
    assert item.payload == {"answer": "umbrella"}
    assert item.payload_schema == _SCHEMA
    assert item.creativity_scoring == scoring


async def test_write_backs_apply_only_over_pending(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    type_id, chatroom_id, _scoring = await _scored_room(sessionmaker, project_id, owner_id)
    scored_id, stalled_id = [
        await _submit(
            sessionmaker,
            project_id=project_id,
            owner_id=owner_id,
            type_id=type_id,
            chatroom_id=chatroom_id,
            answer=answer,
        )
        for answer in ("scored", "stalled")
    ]
    scores = {"fluency": 1, "flexibility": 2, "originality": 3, "convergence": 4}

    async with sessionmaker() as session:
        facade = ActivitiesFacade(session)
        assert await facade.record_judgement(
            submission_id=scored_id, status=JudgeStatus.SCORED, scores=scores, model="m"
        )
        # A second delivery of the same job.
        assert not await facade.record_judgement(
            submission_id=scored_id, status=JudgeStatus.ERROR, reason="judge_unavailable"
        )
        swept = await facade.sweep_stalled_judging(ttl_seconds=0)
        await session.commit()

    assert (stalled_id, chatroom_id) in swept
    assert all(sid != scored_id for sid, _room in swept)
    async with sessionmaker() as session:
        assert await _judge_state(session, scored_id) == (JudgeStatus.SCORED.value, None)
        assert await _judge_state(session, stalled_id) == (JudgeStatus.ERROR.value, JUDGE_TIMEOUT)
        stored = (
            await session.execute(
                sa.select(at.activity_submissions.c.judge_scores, at.activity_submissions.c.judged_at).where(
                    at.activity_submissions.c.id == scored_id
                )
            )
        ).one()
        # A late job after the sweep is a no-op too.
        assert not await ActivitiesFacade(session).record_judgement(
            submission_id=stalled_id, status=JudgeStatus.SCORED, scores=scores
        )
    assert stored.judge_scores == scores
    assert stored.judged_at is not None


async def test_the_dashboard_means_count_each_round_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Two scored attempts in one round count as that round's latest only, and the
    latest judge state is reported even when it is not a score."""
    project_id, owner_id = project
    type_id, chatroom_id, _scoring = await _scored_room(sessionmaker, project_id, owner_id)
    first, second, third = [
        await _submit(
            sessionmaker,
            project_id=project_id,
            owner_id=owner_id,
            type_id=type_id,
            chatroom_id=chatroom_id,
            answer=answer,
        )
        for answer in ("first", "second", "third")
    ]
    async with sessionmaker() as session:
        facade = ActivitiesFacade(session)
        await facade.record_judgement(
            submission_id=first,
            status=JudgeStatus.SCORED,
            scores={"fluency": 1, "flexibility": 1, "originality": 1, "convergence": 1},
        )
        await facade.record_judgement(
            submission_id=second,
            status=JudgeStatus.SCORED,
            scores={"fluency": 3, "flexibility": 2, "originality": 4, "convergence": 0},
        )
        await facade.record_judgement(
            submission_id=third, status=JudgeStatus.ERROR, reason="judge_bad_output"
        )
        await session.commit()

    async with sessionmaker() as session:
        (row,) = await ActivitiesFacade(session).creativity_for_rooms(chatroom_ids=[chatroom_id])
    assert (row.chatroom_id, row.subject_id, row.scored_rounds) == (chatroom_id, owner_id, 1)
    assert (row.fluency, row.flexibility, row.originality, row.convergence) == (3.0, 2.0, 4.0, 0.0)
    assert (row.last_status, row.last_reason) == ("error", "judge_bad_output")


async def _next_round(
    sessionmaker: async_sessionmaker[AsyncSession],
    chatroom_id: uuid.UUID,
    type_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> None:
    # As ending a round does: its open sessions close with it, which frees the
    # subject to open one in the next round.
    async with sessionmaker() as session:
        ended = (
            await session.execute(
                at.activity_activations.update()
                .where(
                    at.activity_activations.c.chatroom_id == chatroom_id,
                    at.activity_activations.c.status == "active",
                )
                .values(status="ended", ended_at=sa.func.now())
                .returning(at.activity_activations.c.id)
            )
        ).scalar_one()
        await ActivitySessionRepository(session).close_open_for_activation(ended)
        await session.execute(
            at.activity_activations.insert().values(
                chatroom_id=chatroom_id, activity_type_id=type_id, started_by_user_id=owner_id
            )
        )
        await session.commit()


async def test_the_dashboard_means_average_across_rounds(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Each round contributes its latest scored attempt; the mean is over rounds."""
    project_id, owner_id = project
    type_id, chatroom_id, _scoring = await _scored_room(sessionmaker, project_id, owner_id)
    round_scores = [
        {"fluency": 1, "flexibility": 1, "originality": 2, "convergence": 0},
        {"fluency": 3, "flexibility": 2, "originality": 4, "convergence": 3},
    ]
    for n, scores in enumerate(round_scores):
        if n:
            await _next_round(sessionmaker, chatroom_id, type_id, owner_id)
        submission_id = await _submit(
            sessionmaker,
            project_id=project_id,
            owner_id=owner_id,
            type_id=type_id,
            chatroom_id=chatroom_id,
            answer=f"round {n}",
        )
        async with sessionmaker() as session:
            await ActivitiesFacade(session).record_judgement(
                submission_id=submission_id, status=JudgeStatus.SCORED, scores=scores
            )
            await session.commit()

    async with sessionmaker() as session:
        (row,) = await ActivitiesFacade(session).creativity_for_rooms(chatroom_ids=[chatroom_id])
    assert row.scored_rounds == 2
    assert (row.fluency, row.flexibility, row.originality, row.convergence) == (2.0, 1.5, 3.0, 1.5)
    assert row.last_status == "scored"


async def test_a_revision_with_scoring_off_does_not_borrow_the_current_rubric(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    type_id, chatroom_id, scoring = await _scored_room(sessionmaker, project_id, owner_id)

    async def _set_scoring(value: dict[str, Any] | None) -> None:
        async with sessionmaker() as session:
            await ActivityTypeRepository(session).update(
                type_id,
                name="judging itest",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config=_VALIDATOR,
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
                bump_version=True,
                creativity_scoring=value,
            )
            await session.commit()

    await _set_scoring(None)
    submission_id = await _submit(
        sessionmaker,
        project_id=project_id,
        owner_id=owner_id,
        type_id=type_id,
        chatroom_id=chatroom_id,
        answer="unscored",
    )
    await _set_scoring({**scoring, "rubric": _RUBRIC_V2})

    async with sessionmaker() as session:
        item = await ActivitiesFacade(session).get_submission_for_judging(submission_id)
    assert item is not None
    assert item.creativity_scoring is None
