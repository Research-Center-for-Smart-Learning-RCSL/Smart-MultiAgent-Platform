"""Round-scoped activity sessions against a real PostgreSQL (0077, AC-3/AC-4/AC-6/AC-9).

Four things here can only be true of a real database, so the unit tier -- which
compiles statements with ``literal_binds`` and never executes one -- cannot see
any of them:

- The ``(activation_id, subject_user_id)`` unique. A constraint that was never
  created and a constraint that rejects the right rows are indistinguishable
  when nothing runs.
- That the same unique *permits* one subject two sessions across two rounds,
  which is the whole point of 0077 and the opposite of what the partial-unique
  it replaced allowed.
- ``count_for_activation``'s aggregate ``FILTER``, which is PostgreSQL-specific
  (backend/CLAUDE.md requires an executing test for exactly this).
- The migration's own backfill and unclaimed-close SQL, imported from the
  migration module rather than retyped, so this proves what 0077 does rather
  than what a copy of it would do.

The backfill test runs inside a transaction it never commits: both statements
are deliberately unscoped (a migration has the table to itself), and a committed
run would close open sessions belonging to whatever else is in the database.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.models import SessionStatus, ValidatorKind
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.infrastructure import tables as t
from contexts.identity.infrastructure.tables import users as users_t

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0077_activity_session_activation.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0077_session_activation", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0077 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0077)


async def _make_room(session: AsyncSession, project_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    """A workspace + chatroom under ``project_id``, uncommitted.

    No teardown of its own: cleanup rides the ``project`` fixture, since
    workspaces cascade from the project and chatrooms from the workspace.
    """
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    await session.execute(
        t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="act-itest")
    )
    await session.execute(
        t.chatrooms.insert().values(
            id=chatroom_id,
            workspace_id=workspace_id,
            name="act-itest",
            guest_token=str(uuid.uuid4()),
            created_by_user_id=created_by,
        )
    )
    return chatroom_id


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> uuid.UUID:
    """A chatroom to hang activations off."""
    project_id, user_id = project
    async with sessionmaker() as session:
        chatroom_id = await _make_room(session, project_id, user_id)
        await session.commit()
    return chatroom_id


async def _make_type(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    return await ActivityTypeRepository(session).create(
        project_id=project_id,
        key=f"itest-{uuid.uuid4().hex[:10]}",
        name="itest",
        payload_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
        retention_days=None,
        expose_payload_to_agent=True,
        echo_includes_content=False,
    )


async def _start_round(
    session: AsyncSession, room: uuid.UUID, type_id: uuid.UUID, by: uuid.UUID
) -> uuid.UUID:
    activation_id = await ActivationRepository(session).create_active(
        chatroom_id=room, activity_type_id=type_id, started_by_user_id=by
    )
    assert activation_id is not None
    return activation_id


class TestRoundScopedIdentity:
    async def test_two_rounds_give_one_subject_two_sessions(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-3. Pre-0077 the key held nothing about *which* run a session
        belonged to, so the second round's submissions continued the first
        round's row and its attempt_no sequence. Keyed on the activation, the
        second round is structurally its own row and each resolves to itself.

        The round is ended the way ``ActivationService.end`` ends one -- the
        activation *and* its sessions, in that order. Ending only the activation
        would leave a subject holding an open session in a finished round, which
        no production path produces and which ``uq_activity_sessions_open``
        (kept by 0077 for forward compatibility, dropped in FU-7) rejects.
        """
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            activations = ActivationRepository(session)
            type_id = await _make_type(session, project_id)

            first = await _start_round(session, room, type_id, user_id)
            first_session = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=first,
            )
            await activations.end(first)
            await repo.close_open_for_activation(first)

            second = await _start_round(session, room, type_id, user_id)
            second_session = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=second,
            )
            await session.commit()

            assert first_session is not None
            assert second_session is not None
            assert first_session != second_session

            # And each round resolves to its own row, whatever the other's state.
            resolved_first = await repo.get_for_activation(activation_id=first, subject_user_id=user_id)
            resolved_second = await repo.get_for_activation(activation_id=second, subject_user_id=user_id)
            assert resolved_first is not None
            assert resolved_second is not None
            assert resolved_first.id == first_session
            assert resolved_second.id == second_session

    async def test_one_round_cannot_hold_two_sessions_for_one_subject(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """The other half of AC-9's constraint: the lazy-open race resolves to
        one row rather than raising, which is what ``ON CONFLICT DO NOTHING``
        against this unique buys."""
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            type_id = await _make_type(session, project_id)
            activation_id = await _start_round(session, room, type_id, user_id)

            first = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=activation_id,
            )
            second = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=activation_id,
            )
            await session.commit()

            assert first is not None
            assert second is None

    async def test_a_closed_session_still_resolves_rather_than_duplicating(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-5's storage half: answering again after the facilitator ended and
        restarted nothing -- the row is found by identity, not by status."""
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            type_id = await _make_type(session, project_id)
            activation_id = await _start_round(session, room, type_id, user_id)
            session_id = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=activation_id,
            )
            assert session_id is not None
            await repo.close(session_id)
            await session.commit()

            found = await repo.get_for_activation(activation_id=activation_id, subject_user_id=user_id)
            assert found is not None
            assert found.id == session_id
            assert found.status is SessionStatus.CLOSED


class TestProgressAndCascade:
    async def test_count_splits_on_the_declaration_not_the_status(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-6, and the executing test the aggregate ``FILTER`` requires.

        Also pins the reason ``completed_at`` is not ``status``: closing the
        sessions must not move anybody into the finished column.
        """
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            type_id = await _make_type(session, project_id)
            activation_id = await _start_round(session, room, type_id, user_id)

            subjects = [user_id, uuid.uuid4(), uuid.uuid4()]
            # Two extra throwaway users, since subject_user_id is an FK.
            for extra in subjects[1:]:
                await session.execute(
                    users_t.insert().values(id=extra, email=f"itest-{extra}@test.invalid", password_hash="x")
                )
            ids = []
            for subject in subjects:
                created = await repo.create_open(
                    activity_type_id=type_id,
                    chatroom_id=room,
                    subject_user_id=subject,
                    activation_id=activation_id,
                )
                assert created is not None
                ids.append(created)
            await repo.set_completed(ids[0], completed=True)
            await session.commit()

            assert await repo.count_for_activation(activation_id) == (1, 2)

            # Ending the round closes every session and moves nobody.
            closed = await repo.close_open_for_activation(activation_id)
            await session.commit()
            assert closed == 3
            assert await repo.count_for_activation(activation_id) == (1, 2)

            for extra in subjects[1:]:
                await session.execute(users_t.delete().where(users_t.c.id == extra))
            await session.commit()

    async def test_the_close_is_bounded_to_its_own_round(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-4: ending one round must not reach into another one's sessions.

        Two rooms running the same type for the same participant, because that
        is the only shape two concurrently-open rounds can take: one activation
        per room is active at a time (``uq_activity_activations_active``) and
        ending a round closes its own sessions, so within a room the second
        round never overlaps the first.

        The same type in both on purpose -- a close that widened to the type
        (the shape of :meth:`close_open_for_type`) would still pass a
        one-room test and fails this one.
        """
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            type_id = await _make_type(session, project_id)
            other_room = await _make_room(session, project_id, user_id)

            first = await _start_round(session, room, type_id, user_id)
            first_session = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=first,
            )
            second = await _start_round(session, other_room, type_id, user_id)
            second_session = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=other_room,
                subject_user_id=user_id,
                activation_id=second,
            )
            await session.commit()
            assert first_session is not None
            assert second_session is not None

            assert await repo.close_open_for_activation(second) == 1
            await session.commit()

            still_open = await repo.get(first_session)
            assert still_open is not None
            assert still_open.status is SessionStatus.OPEN
            assert still_open.closed_at is None

    async def test_the_declaration_is_guarded_and_reversible(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-5: a repeat click reports no transition, so no event is replayed."""
        project_id, user_id = project
        async with sessionmaker() as session:
            repo = ActivitySessionRepository(session)
            type_id = await _make_type(session, project_id)
            activation_id = await _start_round(session, room, type_id, user_id)
            session_id = await repo.create_open(
                activity_type_id=type_id,
                chatroom_id=room,
                subject_user_id=user_id,
                activation_id=activation_id,
            )
            assert session_id is not None
            await session.commit()

            assert await repo.set_completed(session_id, completed=True) is True
            assert await repo.set_completed(session_id, completed=True) is False
            assert await repo.set_completed(session_id, completed=False) is True
            assert await repo.set_completed(session_id, completed=False) is False
            await session.commit()

            final = await repo.get(session_id)
            assert final is not None
            assert final.completed_at is None


class TestMigrationDataSteps:
    async def test_the_backfill_adopts_a_live_round_and_closes_the_rest(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        room: uuid.UUID,
    ) -> None:
        """AC-9's data half, running 0077's own SQL.

        Never committed: both statements are unscoped, as a migration's may be,
        and committing would close open sessions belonging to anything else in
        this database.
        """
        project_id, user_id = project
        async with sessionmaker() as session:
            type_id = await _make_type(session, project_id)
            claimed_type = await _make_type(session, project_id)
            activation_id = await _start_round(session, room, claimed_type, user_id)

            # A pre-0077 session whose room has a live round of its type...
            adopted = uuid.uuid4()
            await session.execute(
                at.activity_sessions.insert().values(
                    id=adopted,
                    activity_type_id=claimed_type,
                    chatroom_id=room,
                    subject_user_id=user_id,
                    activation_id=None,
                    status=SessionStatus.OPEN.value,
                )
            )
            # ...and one whose type nothing is running.
            orphaned = uuid.uuid4()
            await session.execute(
                at.activity_sessions.insert().values(
                    id=orphaned,
                    activity_type_id=type_id,
                    chatroom_id=room,
                    subject_user_id=user_id,
                    activation_id=None,
                    status=SessionStatus.OPEN.value,
                )
            )

            await session.execute(sa.text(migration_0077.BACKFILL_ACTIVATION_SQL))
            await session.execute(sa.text(migration_0077.CLOSE_UNCLAIMED_SQL))

            repo = ActivitySessionRepository(session)
            kept = await repo.get(adopted)
            closed = await repo.get(orphaned)
            assert kept is not None
            assert closed is not None
            # The class under way keeps its history, attached to its round.
            assert kept.activation_id == activation_id
            assert kept.status is SessionStatus.OPEN
            # The one no round claims is retired rather than left for the next
            # round to adopt, which is the defect 0077 exists to end.
            assert closed.activation_id is None
            assert closed.status is SessionStatus.CLOSED
            assert closed.closed_at is not None

            await session.rollback()
