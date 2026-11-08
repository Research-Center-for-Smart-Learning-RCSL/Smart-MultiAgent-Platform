"""The research export's subjects, read through the real listings (AC-7).

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md.

A db-tier test because the property is a join between two tables the unit tier
fakes: a guest's activity session holds its guest session id, as do the
messages it sends, and only a real ``activity_sessions`` row shows that a group
session keeps the column's ``'user'`` default (0098), which the listing must
not report.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import activities as activities_route
from contexts.activities.domain.subject_code import group_subject_code, room_code, subject_code
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.application.research_export_builder import (
    MessageRow,
    ResearchExportBuilder,
    SubmissionRow,
)
from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Room:
    workspace_id: uuid.UUID
    chatroom_id: uuid.UUID
    owner_user_id: uuid.UUID
    activity_type_id: uuid.UUID
    activation_id: uuid.UUID
    guests: tuple[uuid.UUID, uuid.UUID]
    group_id: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    """Two guests, the owner and one member group, each with one submission and,
    for the people, one message."""
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    guests = (uuid.uuid4(), uuid.uuid4())
    group_id = uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="export-itest")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="export-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=owner_id,
                allow_guest_links=True,
            )
        )
        for n, guest_id in enumerate(guests):
            await session.execute(
                ct.guest_sessions.insert().values(
                    id=guest_id,
                    chatroom_id=chatroom_id,
                    display_name=f"Guest {n}",
                    refresh_token_hash=f"itest-{guest_id}",
                )
            )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="export-itest-type",
                name="export itest type",
                # jsonb returns these as zz, answer, because; x-order says answer first.
                payload_schema={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "x-order": 1},
                        "zz": {"type": "string", "x-order": 2},
                        "because": {"type": "string"},
                    },
                },
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
        yield Room(workspace_id, chatroom_id, owner_id, type_id, activation_id, guests, group_id)
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("SET ROLE smap_audit_retention"))
            try:
                await cleanup.execute(
                    text("DELETE FROM audit_logs WHERE actor_user_id = ANY(:ids)"), {"ids": list(guests)}
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


async def _submit(sessionmaker: async_sessionmaker[AsyncSession], room: Room, principal: Principal) -> None:
    transport = httpx.ASGITransport(app=_app(sessionmaker, principal))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/chatrooms/{room.chatroom_id}/activity-submissions",
            json={"activity_type_id": str(room.activity_type_id), "payload": {"answer": "林"}},
        )
    assert response.status_code == 200, response.text


async def _seed(sessionmaker: async_sessionmaker[AsyncSession], room: Room) -> None:
    people = [
        Principal(
            user_id=g, is_admin=False, email_verified=False, is_guest=True, chatroom_id=room.chatroom_id
        )
        for g in room.guests
    ]
    people.append(Principal(user_id=room.owner_user_id, is_admin=False, email_verified=True))
    for principal in people:
        await _submit(sessionmaker, room, principal)

    async with sessionmaker() as session:
        group_session = await ActivitySessionRepository(session).create_open_for_group(
            activity_type_id=room.activity_type_id,
            chatroom_id=room.chatroom_id,
            member_group_id=room.group_id,
            activation_id=room.activation_id,
        )
        assert group_session is not None
        await session.execute(
            at.activity_submissions.insert().values(
                session_id=group_session,
                activity_type_id=room.activity_type_id,
                chatroom_id=room.chatroom_id,
                producer_user_id=room.owner_user_id,
                payload={"answer": "group"},
                attempt_no=1,
            )
        )
        for guest_id in room.guests:
            await session.execute(
                ct.messages.insert().values(
                    chatroom_id=room.chatroom_id, sender_type="guest", sender_id=guest_id, content_md="hi"
                )
            )
        await session.execute(
            ct.messages.insert().values(
                chatroom_id=room.chatroom_id,
                sender_type="user",
                sender_id=room.owner_user_id,
                content_md="hi",
            )
        )
        await session.commit()


async def test_guests_keep_distinct_codes_that_join_transcripts_to_submissions(
    sessionmaker: async_sessionmaker[AsyncSession], room: Room
) -> None:
    await _seed(sessionmaker, room)

    async with sessionmaker() as session:
        listed = await ActivitiesFacade(session).list_submissions_for_research_export(
            chatroom_ids=[room.chatroom_id]
        )
        said = await ConversationFacade(session).list_messages_for_rooms(chatroom_ids=[room.chatroom_id])

    kinds = {(s.subject_user_id or s.subject_member_group_id, s.subject_kind) for s in listed}
    assert kinds == {
        (room.guests[0], "guest"),
        (room.guests[1], "guest"),
        (room.owner_user_id, "user"),
        (room.group_id, "group"),
    }
    assert all(s.payload_fields == ("answer", "zz", "because") for s in listed)
    assert {s.activity_type_id for s in listed} == {room.activity_type_id}

    files = ResearchExportBuilder().build_files(
        workspace_id=room.workspace_id,
        room_count=1,
        submissions=[
            SubmissionRow(
                chatroom_id=s.chatroom_id,
                subject_user_id=s.subject_user_id,
                subject_member_group_id=s.subject_member_group_id,
                activity_type_key=s.activity_type_key,
                attempt_no=s.attempt_no,
                is_valid=s.is_valid,
                error_class=s.error_class,
                latency_ms=s.latency_ms,
                created_at=s.created_at,
                payload=s.payload,
                sub_scores=s.sub_scores,
                subject_kind=s.subject_kind,
                payload_fields=s.payload_fields,
                activity_type_id=s.activity_type_id,
            )
            for s in listed
        ],
        observations=[],
        messages=[
            MessageRow(
                chatroom_id=m.chatroom_id,
                sender_type=m.sender_type.value if hasattr(m.sender_type, "value") else m.sender_type,
                sender_id=m.sender_id,
                content_md=m.content_md,
                created_at=m.created_at,
            )
            for m in said
        ],
        agent_key_map={},
    )
    csv_text = files["submissions/export-itest-type.csv"]
    transcript = json.loads(files["transcripts.json"])[room_code(room.chatroom_id)]
    spoken = {(m["subject_code"], m["subject_kind"]) for m in transcript}
    for guest_id in room.guests:
        code = subject_code(guest_id)
        assert f"{code},guest," in csv_text
        assert (code, "guest") in spoken
    assert f"{subject_code(room.owner_user_id)},user," in csv_text
    assert f"{group_subject_code(room.group_id)},group," in csv_text
    assert subject_code(room.guests[0]) != subject_code(room.guests[1])
