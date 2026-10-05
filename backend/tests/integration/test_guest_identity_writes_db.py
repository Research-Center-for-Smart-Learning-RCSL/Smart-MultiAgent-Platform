"""Guest writes into columns that used to reference ``users`` -- AC-1 to AC-5.

Spec: ``docs/tasks/2026-10-05-guest-identity-foreign-keys/spec.md``.

WHY THESE ARE DB-TIER TESTS
---------------------------
The defect is a foreign-key violation, which only a real database raises. The
unit tests that claimed guest coverage (``TestGuestSubmission``) mocked every
repository and so could never see it; that is how a guest could not submit an
activity for a month while the suite stayed green.

The guest principal is built exactly as ``AuthMiddleware._handle_guest`` builds
it: ``user_id`` is a ``guest_sessions.id``, never a ``users.id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import activities as activities_route
from contexts.activities.domain.models import SubjectKind
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.infrastructure.repositories.attachment_repo import MessageAttachmentRepository
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class GuestRoom:
    project_id: uuid.UUID
    owner_user_id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    activation_id: uuid.UUID
    guest_id: uuid.UUID

    @property
    def guest(self) -> Principal:
        return Principal(
            user_id=self.guest_id,
            is_admin=False,
            email_verified=False,
            is_guest=True,
            chatroom_id=self.chatroom_id,
        )


@pytest.fixture
async def guest_room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[GuestRoom]:
    """A guest-link room with one live round and one anonymous guest.

    Rows ride the project cascade. Audit rows the guest's actions emit carry the
    guest id as actor, which the ``project`` fixture's owner-keyed cleanup does
    not reach, so they are removed here under the same retention role.
    """
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id, guest_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="guest-fk-itest")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="guest-fk-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=owner_id,
                allow_guest_links=True,
            )
        )
        await session.execute(
            ct.guest_sessions.insert().values(
                id=guest_id,
                chatroom_id=chatroom_id,
                display_name="Guest Student",
                refresh_token_hash=f"itest-{guest_id}",
            )
        )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="guest-fk-itest-type",
                name="guest fk itest type",
                validator_kind="in_process",
                validator_config={"validator_id": "filled_count", "min_filled": 1},
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                id=activation_id,
                chatroom_id=chatroom_id,
                activity_type_id=type_id,
                started_by_user_id=owner_id,
            )
        )
        await session.commit()
    try:
        yield GuestRoom(project_id, owner_id, chatroom_id, type_id, activation_id, guest_id)
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("SET ROLE smap_audit_retention"))
            try:
                await cleanup.execute(
                    text("DELETE FROM audit_logs WHERE actor_user_id = :gid"), {"gid": guest_id}
                )
            finally:
                await cleanup.execute(text("RESET ROLE"))
            await cleanup.commit()


def _app(sessionmaker: async_sessionmaker[AsyncSession], principal: Principal) -> FastAPI:
    app = FastAPI()
    app.include_router(activities_route.chatroom_router)

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[db_session] = _db
    app.dependency_overrides[current_principal] = lambda: principal
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _guest_session_row(
    sessionmaker: async_sessionmaker[AsyncSession], room: GuestRoom
) -> sa.Row[tuple[object, ...]]:
    async with sessionmaker() as read:
        return (
            await read.execute(
                sa.select(at.activity_sessions).where(
                    sa.and_(
                        at.activity_sessions.c.activation_id == room.activation_id,
                        at.activity_sessions.c.subject_user_id == room.guest_id,
                    )
                )
            )
        ).one()


class TestGuestActivityParticipation:
    """AC-1 and AC-2: [R30.26] -- a guest is a full activity participant."""

    async def test_a_guest_submission_is_recorded_as_the_guests(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        async with await _client(_app(sessionmaker, guest_room.guest)) as client:
            response = await client.post(
                f"/api/chatrooms/{guest_room.chatroom_id}/activity-submissions",
                json={"activity_type_id": str(guest_room.activity_type_id), "payload": {"answer": "x"}},
            )
        assert response.status_code == 200, response.text

        session_row = await _guest_session_row(sessionmaker, guest_room)
        assert session_row.subject_kind == "guest"
        async with sessionmaker() as read:
            submission = (
                await read.execute(
                    sa.select(at.activity_submissions).where(
                        at.activity_submissions.c.session_id == session_row.id
                    )
                )
            ).one()
        assert submission.producer_user_id == guest_room.guest_id
        assert submission.producer_kind == "guest"

    async def test_a_guest_can_declare_itself_finished(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        async with await _client(_app(sessionmaker, guest_room.guest)) as client:
            response = await client.patch(
                f"/api/chatrooms/{guest_room.chatroom_id}"
                f"/activity-activations/{guest_room.activation_id}/completion",
                json={"completed": True},
            )
        assert response.status_code == 200, response.text
        assert response.json()["subject_kind"] == "guest"

        session_row = await _guest_session_row(sessionmaker, guest_room)
        assert session_row.completed_at is not None

    async def test_a_guest_can_open_its_session(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        async with await _client(_app(sessionmaker, guest_room.guest)) as client:
            response = await client.post(
                f"/api/chatrooms/{guest_room.chatroom_id}/activity-sessions",
                json={"activity_type_id": str(guest_room.activity_type_id)},
            )
        assert response.status_code == 200, response.text
        assert response.json()["subject_kind"] == "guest"

    async def test_a_members_submission_is_still_recorded_as_a_users(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        """The discriminator must not make every subject a guest: the owner is a
        real user, and the kind is what tells the two populations apart once the
        foreign key no longer does."""
        owner = Principal(user_id=guest_room.owner_user_id, is_admin=False, email_verified=True)
        async with await _client(_app(sessionmaker, owner)) as client:
            response = await client.post(
                f"/api/chatrooms/{guest_room.chatroom_id}/activity-submissions",
                json={"activity_type_id": str(guest_room.activity_type_id), "payload": {"answer": "y"}},
            )
        assert response.status_code == 200, response.text
        async with sessionmaker() as read:
            row = (
                await read.execute(
                    sa.select(at.activity_sessions.c.subject_kind).where(
                        sa.and_(
                            at.activity_sessions.c.activation_id == guest_room.activation_id,
                            at.activity_sessions.c.subject_user_id == guest_room.owner_user_id,
                        )
                    )
                )
            ).one()
        assert row.subject_kind == "user"


class TestSubjectKindComesFromTheId:
    """/code-review: the kind is what the id names, not who is calling."""

    async def test_an_admin_acting_for_a_guest_records_a_guest_session(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        admin = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True)
        async with await _client(_app(sessionmaker, admin)) as client:
            response = await client.post(
                f"/api/chatrooms/{guest_room.chatroom_id}/activity-sessions",
                json={
                    "activity_type_id": str(guest_room.activity_type_id),
                    "subject_user_id": str(guest_room.guest_id),
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["subject_kind"] == "guest"
        session_row = await _guest_session_row(sessionmaker, guest_room)
        assert session_row.subject_kind == "guest"


class TestGuestAttachments:
    """AC-3."""

    async def test_a_guest_upload_is_stored_and_binds_to_the_guests_message(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        attachment_id, message_id = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            repo = MessageAttachmentRepository(session)
            await repo.create(
                attachment_id=attachment_id,
                chatroom_id=guest_room.chatroom_id,
                uploaded_by_user_id=guest_room.guest_id,
                filename="notes.txt",
                mime="text/plain",
                size_bytes=5,
                minio_path=f"itest/{attachment_id}",
                expires_at=None,
            )
            await session.execute(
                ct.messages.insert().values(
                    id=message_id,
                    chatroom_id=guest_room.chatroom_id,
                    sender_type="guest",
                    sender_id=guest_room.guest_id,
                    content_md="see attached",
                )
            )
            bound = await repo.bind_to_message(
                attachment_ids=[attachment_id],
                message_id=message_id,
                chatroom_id=guest_room.chatroom_id,
                uploaded_by_user_id=guest_room.guest_id,
            )
            await session.commit()
        assert bound == 1

    async def test_agent_artifact_binding_still_ignores_human_uploads(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        """NULL uploader keeps meaning "agent artifact": a guest upload, now stored
        with the guest id, must not become bindable to an agent message."""
        attachment_id, message_id = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            repo = MessageAttachmentRepository(session)
            await repo.create(
                attachment_id=attachment_id,
                chatroom_id=guest_room.chatroom_id,
                uploaded_by_user_id=guest_room.guest_id,
                filename="notes.txt",
                mime="text/plain",
                size_bytes=5,
                minio_path=f"itest/{attachment_id}",
                expires_at=None,
            )
            await session.execute(
                ct.messages.insert().values(
                    id=message_id,
                    chatroom_id=guest_room.chatroom_id,
                    sender_type="agent",
                    sender_id=uuid.uuid4(),
                    content_md="artifact",
                )
            )
            bound = await repo.bind_agent_artifacts(
                attachment_ids=[attachment_id], message_id=message_id, chatroom_id=guest_room.chatroom_id
            )
            await session.commit()
        assert bound == 0


_USER_FKS = sa.text(
    """
    SELECT conname
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
     WHERE c.contype = 'f'
       AND c.conrelid = CAST(:table AS regclass)
       AND c.confrelid = CAST('users' AS regclass)
       AND a.attname = :column
    """
)


class TestSchema:
    """AC-4."""

    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("activity_sessions", "subject_user_id"),
            ("activity_submissions", "producer_user_id"),
            ("message_attachments", "uploaded_by_user_id"),
        ],
    )
    async def test_no_foreign_key_to_users_remains(
        self, sessionmaker: async_sessionmaker[AsyncSession], table: str, column: str
    ) -> None:
        """Asserted against the catalog rather than by attempting an insert, so a
        migration that dropped a constraint under the wrong name fails here
        instead of passing because ``IF EXISTS`` swallowed the miss."""
        async with sessionmaker() as read:
            names = (await read.execute(_USER_FKS, {"table": table, "column": column})).scalars().all()
        assert names == []

    async def test_an_unknown_subject_kind_is_refused(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        async with sessionmaker() as session:
            with pytest.raises(IntegrityError, match="ck_activity_sessions_subject_kind"):
                await session.execute(
                    at.activity_sessions.insert().values(
                        activity_type_id=guest_room.activity_type_id,
                        chatroom_id=guest_room.chatroom_id,
                        activation_id=guest_room.activation_id,
                        subject_user_id=guest_room.guest_id,
                        subject_kind="robot",
                    )
                )
            await session.rollback()

    async def test_a_group_session_written_the_pre_0098_way_still_reads_as_a_group(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        """Forward compatibility: pre-0098 code opens a group session naming no
        kind. The insert must succeed on the new schema, and the row must still
        read as a group, because the kind column describes persons only."""
        group_id = uuid.uuid4()
        async with sessionmaker() as session:
            session_id = await ActivitySessionRepository(session).create_open_for_group(
                activity_type_id=guest_room.activity_type_id,
                chatroom_id=guest_room.chatroom_id,
                member_group_id=group_id,
                activation_id=guest_room.activation_id,
            )
            await session.commit()
            assert session_id is not None
            opened = await ActivitySessionRepository(session).get(session_id)
        assert opened is not None
        assert opened.subject_kind is SubjectKind.MEMBER_GROUP


class TestUserErasure:
    """AC-5: hard delete removes exactly what the dropped cascade removed."""

    async def test_purge_removes_the_users_rows_and_no_guests(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        guest_room: GuestRoom,
    ) -> None:
        owner = Principal(user_id=guest_room.owner_user_id, is_admin=False, email_verified=True)
        for principal, answer in ((owner, "owner"), (guest_room.guest, "guest")):
            async with await _client(_app(sessionmaker, principal)) as client:
                response = await client.post(
                    f"/api/chatrooms/{guest_room.chatroom_id}/activity-submissions",
                    json={
                        "activity_type_id": str(guest_room.activity_type_id),
                        "payload": {"answer": answer},
                    },
                )
            assert response.status_code == 200, response.text

        async with sessionmaker() as session:
            await ActivitiesFacade(session).purge_user_activity_rows(guest_room.owner_user_id)
            await session.commit()

        async with sessionmaker() as read:
            subjects = (
                await read.execute(
                    sa.select(
                        at.activity_sessions.c.subject_user_id, at.activity_sessions.c.subject_kind
                    ).where(at.activity_sessions.c.activation_id == guest_room.activation_id)
                )
            ).all()
            producers = (
                (
                    await read.execute(
                        sa.select(at.activity_submissions.c.producer_user_id).where(
                            at.activity_submissions.c.chatroom_id == guest_room.chatroom_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [(s.subject_user_id, s.subject_kind) for s in subjects] == [(guest_room.guest_id, "guest")]
        assert producers == [guest_room.guest_id]

    async def test_clearing_the_uploader_nulls_only_that_users_uploads(
        self, sessionmaker: async_sessionmaker[AsyncSession], guest_room: GuestRoom
    ) -> None:
        """The SET NULL the dropped foreign key performed on hard delete."""
        owner_upload, guest_upload = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            repo = MessageAttachmentRepository(session)
            for attachment_id, uploader in (
                (owner_upload, guest_room.owner_user_id),
                (guest_upload, guest_room.guest_id),
            ):
                await repo.create(
                    attachment_id=attachment_id,
                    chatroom_id=guest_room.chatroom_id,
                    uploaded_by_user_id=uploader,
                    filename="f.txt",
                    mime="text/plain",
                    size_bytes=1,
                    minio_path=f"itest/{attachment_id}",
                    expires_at=None,
                )
            await session.commit()

        async with sessionmaker() as session:
            cleared = await ConversationFacade(session).clear_attachment_uploader(guest_room.owner_user_id)
            await session.commit()

        async with sessionmaker() as read:
            rows = (
                await read.execute(
                    sa.select(
                        ct.message_attachments.c.id, ct.message_attachments.c.uploaded_by_user_id
                    ).where(ct.message_attachments.c.id.in_([owner_upload, guest_upload]))
                )
            ).all()
        uploaders: dict[uuid.UUID, uuid.UUID | None] = {row.id: row.uploaded_by_user_id for row in rows}
        assert cleared == 1
        assert uploaders == {owner_upload: None, guest_upload: guest_room.guest_id}
