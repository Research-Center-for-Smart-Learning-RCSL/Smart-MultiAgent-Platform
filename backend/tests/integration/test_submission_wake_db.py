"""The submission wake's counts, against PostgreSQL -- AC-10.

``valid_counts_since`` uses an aggregate ``FILTER`` clause, which the unit tier
compiles but never executes (``backend/CLAUDE.md``). This runs it: valid rows
only, this round only, soft-deleted rows excluded, and the window split by
``created_at``.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.submission_repo import ActivitySubmissionRepository
from contexts.conversation.infrastructure import tables as ct

pytestmark = pytest.mark.db


async def test_valid_counts_since_splits_the_window_from_the_round(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    workspace_id, room = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id, other_activation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session_id, other_session = uuid.uuid4(), uuid.uuid4()
    window = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
    async with sessionmaker() as s:
        await s.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="wake-itest")
        )
        await s.execute(
            ct.chatrooms.insert().values(
                id=room,
                workspace_id=workspace_id,
                name="wake-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=owner_id,
            )
        )
        await s.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="wake-itest-type",
                name="wake itest type",
                validator_kind="in_process",
                validator_config={"validator_id": "filled_count", "min_filled": 1},
            )
        )
        for aid, status in ((other_activation, "ended"), (activation_id, "active")):
            await s.execute(
                at.activity_activations.insert().values(
                    id=aid,
                    chatroom_id=room,
                    activity_type_id=type_id,
                    started_by_user_id=owner_id,
                    status=status,
                )
            )
        for sid, aid, status in (
            (other_session, other_activation, "closed"),
            (session_id, activation_id, "open"),
        ):
            await s.execute(
                at.activity_sessions.insert().values(
                    id=sid,
                    activity_type_id=type_id,
                    chatroom_id=room,
                    activation_id=aid,
                    subject_user_id=owner_id,
                    status=status,
                )
            )
        rows = [
            # (session, valid, created, deleted)
            (session_id, True, window - dt.timedelta(minutes=5), False),  # earlier in the round
            (session_id, True, window + dt.timedelta(seconds=2), False),  # in the window
            (session_id, True, window + dt.timedelta(seconds=4), False),  # in the window
            (session_id, False, window + dt.timedelta(seconds=5), False),  # invalid
            (session_id, True, window + dt.timedelta(seconds=6), True),  # soft-deleted
            (other_session, True, window + dt.timedelta(seconds=3), False),  # another round
        ]
        for attempt, (sid, valid, created, deleted) in enumerate(rows, start=1):
            await s.execute(
                at.activity_submissions.insert().values(
                    id=uuid.uuid4(),
                    session_id=sid,
                    activity_type_id=type_id,
                    chatroom_id=room,
                    producer_user_id=owner_id,
                    attempt_no=attempt,
                    validation_status="validated",
                    is_valid=valid,
                    created_at=created,
                    deleted_at=created if deleted else None,
                )
            )
        await s.commit()

    async with sessionmaker() as read:
        counts = await ActivitySubmissionRepository(read).valid_counts_since(
            activation_id=activation_id, since=window
        )

    assert counts == (2, 3)
