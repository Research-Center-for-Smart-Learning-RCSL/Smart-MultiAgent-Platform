"""Activity type revisions and the version a submission records (AC-10, AC-11).

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Versioning).

A db-tier test because both properties live in PostgreSQL: the revision row is
written in the type's own transaction, and the ordering of a submission against
a concurrent behavioural edit is a PostgreSQL advisory lock that a faked
repository cannot take. The same lock must not hold up a metadata edit (D-16).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.domain.models import ValidatorKind
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.infrastructure import tables as ct

pytestmark = pytest.mark.db

_SCHEMA_V1: dict[str, Any] = {"type": "object", "properties": {"answer": {"type": "string"}}}
_SCHEMA_V2: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "reason": {"type": "string"}},
}
_VALIDATOR: dict[str, Any] = {"validator_id": "filled_count", "min_filled": 1}
# Long enough that an edit which is not blocked has certainly finished.
_BLOCKED_PROBE_SECONDS = 1.0


async def _register(session: AsyncSession, project_id: uuid.UUID, owner_id: uuid.UUID) -> uuid.UUID:
    registration = await ActivitiesFacade(session).register_type(
        project_id=project_id,
        key=f"revisions-itest-{uuid.uuid4().hex[:8]}",
        name="revisions itest",
        payload_schema=_SCHEMA_V1,
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config=_VALIDATOR,
        retention_days=None,
        actor_user_id=owner_id,
        actor_ip=None,
    )
    return registration.activity_type.id


async def _update(
    session: AsyncSession,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    type_id: uuid.UUID,
    *,
    name: str,
    payload_schema: dict[str, Any],
) -> int:
    updated = await ActivitiesFacade(session).update_type(
        project_id=project_id,
        type_id=type_id,
        name=name,
        payload_schema=payload_schema,
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config=_VALIDATOR,
        retention_days=None,
        actor_user_id=owner_id,
        actor_ip=None,
    )
    return updated.version


async def _revisions(session: AsyncSession, type_id: uuid.UUID) -> list[Any]:
    rows = await session.execute(
        sa.select(at.activity_type_revisions)
        .where(at.activity_type_revisions.c.activity_type_id == type_id)
        .order_by(at.activity_type_revisions.c.version)
    )
    return list(rows.all())


async def test_each_definition_gets_one_revision_and_metadata_edits_none(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, owner_id = project
    async with sessionmaker() as session:
        type_id = await _register(session, project_id, owner_id)
        await session.commit()
    async with sessionmaker() as session:
        (first,) = await _revisions(session, type_id)
    assert (first.version, first.payload_schema, first.created_by) == (1, _SCHEMA_V1, owner_id)

    async with sessionmaker() as session:
        version = await _update(
            session, project_id, owner_id, type_id, name="renamed", payload_schema=_SCHEMA_V1
        )
        await session.commit()
    assert version == 1
    async with sessionmaker() as session:
        assert [r.version for r in await _revisions(session, type_id)] == [1]

    async with sessionmaker() as session:
        version = await _update(
            session, project_id, owner_id, type_id, name="renamed", payload_schema=_SCHEMA_V2
        )
        await session.commit()
    assert version == 2
    async with sessionmaker() as session:
        first, second = await _revisions(session, type_id)
    # Version 1 is untouched, and version 2 is the new definition.
    assert first.payload_schema == _SCHEMA_V1
    assert second.version == 2
    assert second.payload_schema == _SCHEMA_V2
    assert second.validator_kind == ValidatorKind.IN_PROCESS.value
    assert second.validator_config == _VALIDATOR
    assert second.group_config is None
    assert second.created_by == owner_id


@dataclass(frozen=True)
class Room:
    project_id: uuid.UUID
    owner_id: uuid.UUID
    chatroom_id: uuid.UUID
    type_id: uuid.UUID


@pytest.fixture
async def active_room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> Room:
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        type_id = await _register(session, project_id, owner_id)
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="revisions-itest")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="revisions-itest",
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
    return Room(project_id, owner_id, chatroom_id, type_id)


async def test_a_concurrent_edit_waits_for_the_submission_it_would_change(
    sessionmaker: async_sessionmaker[AsyncSession],
    active_room: Room,
) -> None:
    """The edit is issued through the repository, past the service's live-activation
    guard, because that guard is a plain read an activation can slip past (FU-5);
    the lock is what has to hold when it does."""
    room = active_room
    submitter = sessionmaker()
    editor = sessionmaker()
    try:
        submission, _signal = await ActivitiesFacade(submitter).submit(
            project_id=room.project_id,
            activity_type_id=room.type_id,
            chatroom_id=room.chatroom_id,
            producer_user_id=room.owner_id,
            subject_user_id=room.owner_id,
            caller_user_id=None,
            payload={"answer": "first"},
            actor_user_id=room.owner_id,
            actor_ip=None,
        )

        async def _edit() -> None:
            await ActivityTypeRepository(editor).update(
                room.type_id,
                name="edited",
                payload_schema=_SCHEMA_V2,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config=_VALIDATOR,
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
                bump_version=True,
                actor_user_id=room.owner_id,
            )
            await editor.commit()

        edit = asyncio.create_task(_edit())
        await asyncio.sleep(_BLOCKED_PROBE_SECONDS)
        assert not edit.done(), "the behavioural edit did not wait for the open submission"

        await submitter.commit()
        await asyncio.wait_for(edit, timeout=10)
    finally:
        await submitter.close()
        await editor.close()

    async with sessionmaker() as session:
        recorded = (
            await session.execute(
                sa.select(at.activity_submissions.c.type_version).where(
                    at.activity_submissions.c.id == submission.id
                )
            )
        ).scalar_one()
        current = await ActivityTypeRepository(session).get(room.type_id)
    assert recorded == 1
    assert current is not None
    assert current.version == 2

    # The export reads the schema the row was submitted under, not the current one.
    async with sessionmaker() as session:
        (listed,) = await ActivitiesFacade(session).list_submissions_for_research_export(
            chatroom_ids=[room.chatroom_id]
        )
    assert (listed.type_version, listed.payload_fields) == (1, ("answer",))


async def test_a_metadata_edit_does_not_wait_for_an_open_submission(
    sessionmaker: async_sessionmaker[AsyncSession],
    active_room: Room,
) -> None:
    """A governance flag must stay editable while classes submit ([R30.23], D-16)."""
    room = active_room
    submitter = sessionmaker()
    editor = sessionmaker()
    try:
        await ActivitiesFacade(submitter).submit(
            project_id=room.project_id,
            activity_type_id=room.type_id,
            chatroom_id=room.chatroom_id,
            producer_user_id=room.owner_id,
            subject_user_id=room.owner_id,
            caller_user_id=None,
            payload={"answer": "open"},
            actor_user_id=room.owner_id,
            actor_ip=None,
        )

        async def _edit() -> None:
            await ActivityTypeRepository(editor).update(
                room.type_id,
                name="renamed",
                payload_schema=_SCHEMA_V1,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config=_VALIDATOR,
                retention_days=None,
                expose_payload_to_agent=False,
                echo_includes_content=False,
                bump_version=False,
            )
            await editor.commit()

        await asyncio.wait_for(_edit(), timeout=_BLOCKED_PROBE_SECONDS * 5)
        await submitter.commit()
    finally:
        await submitter.close()
        await editor.close()


async def test_a_submission_after_an_edit_records_the_new_version(
    sessionmaker: async_sessionmaker[AsyncSession],
    active_room: Room,
) -> None:
    room = active_room
    async with sessionmaker() as session:
        await ActivityTypeRepository(session).update(
            room.type_id,
            name="edited",
            payload_schema=_SCHEMA_V2,
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config=_VALIDATOR,
            retention_days=None,
            expose_payload_to_agent=True,
            echo_includes_content=False,
            bump_version=True,
        )
        await session.commit()

    async with sessionmaker() as session:
        await ActivitiesFacade(session).submit(
            project_id=room.project_id,
            activity_type_id=room.type_id,
            chatroom_id=room.chatroom_id,
            producer_user_id=room.owner_id,
            subject_user_id=room.owner_id,
            caller_user_id=None,
            payload={"answer": "second"},
            actor_user_id=room.owner_id,
            actor_ip=None,
        )
        await session.commit()

    async with sessionmaker() as session:
        listed = await ActivitiesFacade(session).list_submissions_for_research_export(
            chatroom_ids=[room.chatroom_id]
        )
    assert [(s.type_version, s.payload_fields) for s in listed] == [(2, ("answer", "reason"))]
