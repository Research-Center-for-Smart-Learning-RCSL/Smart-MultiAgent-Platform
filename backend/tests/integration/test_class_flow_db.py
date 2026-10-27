"""Phase 4 class-flow SQL against a real PostgreSQL ([R15.27], [R15.28], [R30.46]).

What only an executing database can show, and the unit tier (``literal_binds``,
nothing runs) cannot:

- ``make_interval`` takes the minutes as its sixth positional argument and an
  integer bind; a wrong position or type is a valid-looking statement that
  either raises or computes hours.
- The deadline claim's guarded UPDATE over a ``FOR UPDATE SKIP LOCKED``
  subquery claims once, ignores a round that is not yet due, and never returns
  a round ended before its deadline.
- The all-submitted claim and the class-start claim are once-only.
- The distinct-subject read counts people, not submissions, and ignores invalid,
  deleted and group rows.
- ``ClassStartRepository.write`` clears the JSONB setting to SQL NULL, not the
  JSON literal ``null``, which ``IS NOT NULL`` would keep in the sweep's index.

The deadline claim is unscoped by design (one sweep serves every room), so
those tests run in a transaction they roll back and assert only on their own
rows: a committed run would claim deadlines belonging to anything else here.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.models import ActivationStatus, ValidatorKind
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.submission_repo import ActivitySubmissionRepository
from contexts.activities.infrastructure.repositories.time_limit_repo import RoomTimeLimitRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.domain.models import ClassStartConfig
from contexts.conversation.infrastructure import tables as t
from contexts.conversation.infrastructure.repositories.class_start_repo import ClassStartRepository
from contexts.identity.infrastructure.tables import users as users_t

pytestmark = pytest.mark.db


async def _make_room(session: AsyncSession, project_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    """Uncommitted; cleanup rides the ``project`` fixture through the workspace cascade."""
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    await session.execute(
        t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="p4-itest")
    )
    await session.execute(
        t.chatrooms.insert().values(
            id=chatroom_id,
            workspace_id=workspace_id,
            name="p4-itest",
            guest_token=str(uuid.uuid4()),
            created_by_user_id=created_by,
        )
    )
    return chatroom_id


async def _make_type(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    return await ActivityTypeRepository(session).create(
        project_id=project_id,
        key=f"p4-itest-{uuid.uuid4().hex[:10]}",
        name="p4-itest",
        payload_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
        retention_days=None,
        expose_payload_to_agent=True,
        echo_includes_content=False,
    )


async def _start(
    session: AsyncSession, room: uuid.UUID, type_id: uuid.UUID, by: uuid.UUID, minutes: int | None
) -> uuid.UUID:
    activation_id = await ActivationRepository(session).create_active(
        chatroom_id=room, activity_type_id=type_id, started_by_user_id=by, time_limit_minutes=minutes
    )
    assert activation_id is not None
    return activation_id


async def _backdate(session: AsyncSession, activation_id: uuid.UUID, *, deadline_ago: dt.timedelta) -> None:
    await session.execute(
        at.activity_activations.update()
        .where(at.activity_activations.c.id == activation_id)
        .values(deadline_at=sa.func.now() - deadline_ago)
    )


class TestDeadlineSnapshot:
    async def test_the_deadline_is_start_plus_the_limit_in_minutes(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            type_id = await _make_type(session, project_id)
            activation_id = await _start(session, room, type_id, user_id, 7)
            row = (
                await session.execute(
                    sa.select(
                        at.activity_activations.c.created_at,
                        at.activity_activations.c.deadline_at,
                        at.activity_activations.c.time_limit_minutes,
                    ).where(at.activity_activations.c.id == activation_id)
                )
            ).one()
            await session.rollback()

        # Same now() for both columns, so the difference is exact, not approximate.
        assert row.deadline_at - row.created_at == dt.timedelta(minutes=7)
        assert row.time_limit_minutes == 7

    async def test_no_limit_leaves_no_deadline(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            type_id = await _make_type(session, project_id)
            activation_id = await _start(session, room, type_id, user_id, None)
            got = await ActivationRepository(session).get(activation_id)
            await session.rollback()

        assert got is not None
        assert (got.time_limit_minutes, got.deadline_at) == (None, None)


class TestDeadlineClaim:
    async def test_a_due_round_is_claimed_once_and_others_are_left(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivationRepository(session)
            type_id = await _make_type(session, project_id)
            due = await _start(session, await _make_room(session, project_id, user_id), type_id, user_id, 5)
            later = await _start(session, await _make_room(session, project_id, user_id), type_id, user_id, 5)
            ended = await _start(session, await _make_room(session, project_id, user_id), type_id, user_id, 5)
            await _backdate(session, due, deadline_ago=dt.timedelta(seconds=30))
            # Ended before its deadline, which has since passed: never a time-limit wake.
            assert await repo.end(ended)
            await _backdate(session, ended, deadline_ago=dt.timedelta(seconds=30))

            first = {a.id for a in await repo.claim_due_deadlines(limit=500)}
            second = {a.id for a in await repo.claim_due_deadlines(limit=500)}
            claimed = await repo.get(due)
            await session.rollback()

        assert due in first
        assert later not in first
        assert ended not in first
        # The second sweep finds the round already claimed.
        assert due not in second
        assert claimed is not None
        # Claiming notifies; it does not end the round.
        assert claimed.status is ActivationStatus.ACTIVE

    async def test_the_completion_claim_fires_once_and_not_after_the_end(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivationRepository(session)
            type_id = await _make_type(session, project_id)
            live = await _start(
                session, await _make_room(session, project_id, user_id), type_id, user_id, None
            )
            ended = await _start(
                session, await _make_room(session, project_id, user_id), type_id, user_id, None
            )
            assert await repo.end(ended)

            results = (await repo.claim_completion(live), await repo.claim_completion(live))
            after_end = await repo.claim_completion(ended)
            await session.rollback()

        assert results == (True, False)
        assert after_end is False


class TestValidIndividualSubjects:
    async def test_people_with_a_valid_submission_are_counted_once(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        invalid_only, deleted_only = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            for extra in (invalid_only, deleted_only):
                await session.execute(
                    users_t.insert().values(
                        id=extra, email=f"p4-itest-{extra}@test.invalid", password_hash="x"
                    )
                )
            room = await _make_room(session, project_id, user_id)
            type_id = await _make_type(session, project_id)
            activation_id = await _start(session, room, type_id, user_id, None)
            sessions = ActivitySessionRepository(session)

            async def submit(subject: uuid.UUID, attempts: list[tuple[bool, bool]]) -> None:
                session_id = await sessions.create_open(
                    activity_type_id=type_id,
                    chatroom_id=room,
                    subject_user_id=subject,
                    activation_id=activation_id,
                )
                assert session_id is not None
                for n, (valid, deleted) in enumerate(attempts, start=1):
                    await session.execute(
                        at.activity_submissions.insert().values(
                            id=uuid.uuid4(),
                            session_id=session_id,
                            activity_type_id=type_id,
                            chatroom_id=room,
                            producer_user_id=subject,
                            attempt_no=n,
                            validation_status="validated",
                            is_valid=valid,
                            deleted_at=dt.datetime.now(dt.UTC) if deleted else None,
                        )
                    )

            # Two valid attempts from one person are still one person.
            await submit(user_id, [(True, False), (True, False)])
            await submit(invalid_only, [(False, False)])
            await submit(deleted_only, [(True, True)])

            subjects = await ActivitySubmissionRepository(session).valid_individual_subjects(
                activation_id=activation_id
            )
            await session.rollback()

        assert subjects == {user_id}


class TestRoomTimeLimits:
    async def test_replace_is_exact_and_keeps_an_explicit_no_limit(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            a, b = await _make_type(session, project_id), await _make_type(session, project_id)
            repo = RoomTimeLimitRepository(session)

            await repo.replace(room, {a: 8, b: None})
            both = await repo.for_room(room)
            await repo.replace(room, {b: 4})
            one = await repo.for_room(room)
            await repo.replace(room, {})
            none = await repo.for_room(room)
            await session.rollback()

        assert both == {a: 8, b: None}
        assert one == {b: 4}
        assert none == {}


class TestClassStartRepository:
    async def test_first_arrival_is_recorded_once_and_the_start_is_claimed_once(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            repo = ClassStartRepository(session)
            config = ClassStartConfig(enabled=True, min_participants=3, max_wait_minutes=2)

            unarmed_claim = await repo.claim_start(room)
            await repo.write(room, config=config, reset=False)
            await repo.record_first_arrival(room)
            first = await repo.get(room)
            await repo.record_first_arrival(room)
            again = await repo.get(room)
            waiting = {s.chatroom_id for s in await repo.awaiting_start(limit=500)}
            claims = (await repo.claim_start(room), await repo.claim_start(room))
            waiting_after = {s.chatroom_id for s in await repo.awaiting_start(limit=500)}
            await session.rollback()

        assert unarmed_claim is False
        assert first is not None
        assert again is not None
        assert first.config == config
        assert first.first_arrival_at is not None
        assert again.first_arrival_at == first.first_arrival_at
        assert room in waiting
        assert claims == (True, False)
        assert room not in waiting_after

    async def test_a_disabled_room_is_neither_swept_nor_started(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            repo = ClassStartRepository(session)
            on = ClassStartConfig(enabled=True, min_participants=3, max_wait_minutes=2)
            await repo.write(room, config=on, reset=False)
            await repo.record_first_arrival(room)
            # Switched off mid-wait, without a reset.
            await repo.write(
                room,
                config=ClassStartConfig(enabled=False, min_participants=3, max_wait_minutes=2),
                reset=False,
            )

            waiting = {s.chatroom_id for s in await repo.awaiting_start(limit=500)}
            claimed = await repo.claim_start(room)
            await session.rollback()

        assert room not in waiting
        assert claimed is False

    async def test_reset_rearms_and_clearing_stores_sql_null(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, user_id = project
        async with sessionmaker() as session:
            room = await _make_room(session, project_id, user_id)
            repo = ClassStartRepository(session)
            config = ClassStartConfig(enabled=True, min_participants=2, max_wait_minutes=5)
            await repo.write(room, config=config, reset=False)
            await repo.record_first_arrival(room)
            assert await repo.claim_start(room)

            kept = await repo.write(room, config=config, reset=False)
            rearmed = await repo.write(room, config=config, reset=True)
            reclaim = await repo.claim_start(room)

            await repo.write(room, config=None, reset=True)
            stored_null = (
                await session.execute(
                    sa.select(t.chatrooms.c.class_start_config.is_(None)).where(t.chatrooms.c.id == room)
                )
            ).scalar_one()
            await session.rollback()

        assert kept is not None
        assert kept.started_at is not None
        assert rearmed is not None
        assert (rearmed.started_at, rearmed.first_arrival_at) == (None, None)
        assert reclaim is True
        assert stored_null is True
