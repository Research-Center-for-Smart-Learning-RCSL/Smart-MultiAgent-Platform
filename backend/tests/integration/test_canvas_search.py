"""Real-Postgres proof that canvas FTS works ([R13.62]/[R13.63]).

The unit tier compiles the query with literal_binds, which hides the
REGCONFIG cast bug (see backend/CLAUDE.md). These tests run the actual
query against asyncpg to verify:
- the trigger populates content_tsv on INSERT
- the trigger updates content_tsv on UPDATE
- deleted objects disappear from search
- REGCONFIG cast executes without error
- only text-bearing objects appear in results
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.canvas.infrastructure import tables as t
from contexts.canvas.infrastructure.repositories.canvas_repo import CanvasRepository

pytestmark = pytest.mark.db

_WORKSPACE_NAME = "canvas-search-itest"


@pytest.fixture
async def canvas_room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> tuple[uuid.UUID, uuid.UUID]:
    """(canvas_id, chatroom_id) with a workspace chain for FK satisfaction."""
    from contexts.conversation.infrastructure.tables import chatrooms, workspaces

    project_id, user_id = project
    ws_id, room_id, canvas_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with sessionmaker() as session:
        await session.execute(
            workspaces.insert().values(id=ws_id, project_id=project_id, name=_WORKSPACE_NAME)
        )
        await session.execute(
            chatrooms.insert().values(
                id=room_id,
                workspace_id=ws_id,
                name="canvas-search-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(t.canvases.insert().values(id=canvas_id, chatroom_id=room_id))
        await session.commit()

    return canvas_id, room_id


async def _insert_object(
    session: AsyncSession,
    canvas_id: uuid.UUID,
    kind: str,
    content: str | None,
) -> uuid.UUID:
    oid = uuid.uuid4()
    await session.execute(
        t.canvas_objects.insert().values(
            id=oid,
            canvas_id=canvas_id,
            kind=kind,
            content=content,
            position_x=0.0,
            position_y=0.0,
            width=100.0,
            height=100.0,
            z_index=0,
            style={},
        )
    )
    return oid


async def test_trigger_populates_content_tsv_on_insert(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        oid = await _insert_object(session, canvas_id, "note", "quarterly revenue report")
        await session.commit()

        row = (
            await session.execute(
                sa.select(t.canvas_objects.c.content_tsv).where(t.canvas_objects.c.id == oid)
            )
        ).scalar_one()
        assert row is not None


async def test_trigger_nulls_tsv_for_null_content(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        oid = await _insert_object(session, canvas_id, "image", None)
        await session.commit()

        row = (
            await session.execute(
                sa.select(t.canvas_objects.c.content_tsv).where(t.canvas_objects.c.id == oid)
            )
        ).scalar_one_or_none()
        assert row is None


async def test_trigger_updates_tsv_on_content_change(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        oid = await _insert_object(session, canvas_id, "note", "old content")
        await session.commit()

        await session.execute(
            t.canvas_objects.update()
            .where(t.canvas_objects.c.id == oid)
            .values(content="new searchable content")
        )
        await session.commit()

        repo = CanvasRepository(session)
        results = await repo.search(canvas_id, "searchable", limit=10)
        assert len(results) == 1
        obj, _rank, _snippet = results[0]
        assert obj.id == oid


async def test_deleted_object_not_in_search(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        oid = await _insert_object(session, canvas_id, "note", "ephemeral content")
        await session.commit()

        results = await CanvasRepository(session).search(canvas_id, "ephemeral", limit=10)
        assert len(results) == 1

        await session.execute(t.canvas_objects.delete().where(t.canvas_objects.c.id == oid))
        await session.commit()

        results = await CanvasRepository(session).search(canvas_id, "ephemeral", limit=10)
        assert len(results) == 0


async def test_search_excludes_non_text_objects(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        await _insert_object(session, canvas_id, "note", "searchable note content")
        await _insert_object(session, canvas_id, "image", None)
        await _insert_object(session, canvas_id, "shape", None)
        await _insert_object(session, canvas_id, "drawing", None)
        await _insert_object(session, canvas_id, "connector", None)
        await session.commit()

        results = await CanvasRepository(session).search(canvas_id, "searchable", limit=50)
        assert len(results) == 1
        obj, _rank, _snippet = results[0]
        assert obj.kind.value == "note"


async def test_search_respects_canvas_id_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    other_canvas_id = uuid.uuid4()

    async with sessionmaker() as session:
        await _insert_object(session, canvas_id, "note", "canvas one content")
        await session.commit()

        results = await CanvasRepository(session).search(other_canvas_id, "canvas", limit=10)
        assert len(results) == 0

        results = await CanvasRepository(session).search(canvas_id, "canvas", limit=10)
        assert len(results) == 1


async def test_regconfig_cast_executes(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """The REGCONFIG cast that prevents the varchar overload error actually
    executes against asyncpg without raising."""
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        await _insert_object(session, canvas_id, "text", "test content for regconfig")
        await session.commit()

        results = await CanvasRepository(session).search(canvas_id, "regconfig", limit=10)
        assert len(results) == 1


async def test_search_returns_snippet_with_mark_tags(
    sessionmaker: async_sessionmaker[AsyncSession],
    canvas_room: tuple[uuid.UUID, uuid.UUID],
) -> None:
    canvas_id, _ = canvas_room
    async with sessionmaker() as session:
        await _insert_object(session, canvas_id, "note", "important project milestone achieved")
        await session.commit()

        results = await CanvasRepository(session).search(canvas_id, "milestone", limit=10)
        assert len(results) == 1
        _obj, _rank, snippet = results[0]
        assert "<mark>milestone</mark>" in snippet
        assert "<b>" not in snippet
