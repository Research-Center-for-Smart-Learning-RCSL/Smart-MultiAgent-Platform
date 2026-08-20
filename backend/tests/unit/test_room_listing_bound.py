"""The visibility-filtered listings read a bounded candidate set, loudly (AC-5).

The four access flags are evaluated in Python, in one place, so the listings pull
candidate rooms and filter in memory rather than keeping a second copy of the
rule in SQL. That trade is only defensible while the read is bounded — and a
confidentiality filter that silently stops is worse than one that never ran,
because a short list reads as a complete one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from contexts.conversation.infrastructure.repositories.chatroom_repo import ChatroomRepository
from contexts.conversation.interfaces import facade as facade_mod

_WORKSPACE_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
_PROJECT_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")


def _row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=_WORKSPACE_ID,
        name="room",
        allow_org_members=False,
        allow_project_members=True,
        allow_project_owners_only=False,
        allow_guest_links=False,
        guest_token="t",
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
        created_by_user_id=None,
        disclose_observers=True,
        parent_project_id=_PROJECT_ID,
    )


def _repo_returning(rows: list[SimpleNamespace]) -> ChatroomRepository:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    return ChatroomRepository(db)


@pytest.mark.asyncio
async def test_truncation_is_detected_and_the_extra_row_is_not_returned() -> None:
    """The query asks for limit + 1 so a full page is not mistaken for the end."""
    repo = _repo_returning([_row() for _ in range(4)])

    rooms, truncated = await repo.list_candidates(workspace_ids=[_WORKSPACE_ID], limit=3)

    assert truncated is True
    assert len(rooms) == 3


@pytest.mark.asyncio
async def test_a_full_page_that_is_the_whole_set_is_not_reported_as_truncated() -> None:
    repo = _repo_returning([_row() for _ in range(3)])

    rooms, truncated = await repo.list_candidates(workspace_ids=[_WORKSPACE_ID], limit=3)

    assert truncated is False
    assert len(rooms) == 3


@pytest.mark.asyncio
async def test_an_empty_scope_is_not_a_query() -> None:
    db = AsyncMock()
    repo = ChatroomRepository(db)

    assert await repo.list_candidates(project_ids=[], limit=10) == ([], False)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_passing_both_scopes_is_refused_rather_than_silently_unioned() -> None:
    repo = ChatroomRepository(AsyncMock())

    with pytest.raises(ValueError, match="exactly one"):
        await repo.list_candidates(workspace_ids=[_WORKSPACE_ID], project_ids=[_PROJECT_ID], limit=10)

    with pytest.raises(ValueError, match="exactly one"):
        await repo.list_candidates(limit=10)


@pytest.mark.asyncio
async def test_the_candidate_query_compiles_for_postgresql() -> None:
    """The unit tier cannot execute SQL, but it can refuse to ship SQL that will not
    compile — a bad label or a broken join is caught here rather than on every request.

    It is *not* a substitute for executing the statement; `backend/CLAUDE.md` is
    explicit that inline literals hide parameter-type errors this tier can never
    see. FU-5 of the dossier carries the integration test that runs it for real.
    """
    captured: list[object] = []

    db = AsyncMock()

    async def _capture(stmt: object) -> SimpleNamespace:
        captured.append(stmt)
        return SimpleNamespace(all=lambda: [])

    db.execute = AsyncMock(side_effect=_capture)

    await ChatroomRepository(db).list_candidates(project_ids=[_PROJECT_ID], limit=10)

    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "JOIN workspaces" in sql
    assert "parent_project_id" in sql
    # Both soft-delete filters must survive: a deleted workspace's rooms are not
    # candidates, and neither are deleted rooms in a live workspace.
    assert sql.count("deleted_at IS NULL") == 2


@pytest.mark.asyncio
async def test_hitting_the_ceiling_warns_and_names_what_was_dropped() -> None:
    rooms = [_row() for _ in range(2)]
    repo = AsyncMock()
    repo.list_candidates = AsyncMock(return_value=([(_PROJECT_ID, r) for r in rooms], True))
    bound_logger = MagicMock()

    with (
        patch.object(facade_mod, "ChatroomRepository", return_value=repo),
        patch.object(facade_mod, "logger", MagicMock(bind=MagicMock(return_value=bound_logger))),
        patch(
            "contexts.conversation.application.access.visible_room_ids",
            AsyncMock(return_value=set()),
        ),
    ):
        await facade_mod.ConversationFacade(AsyncMock()).visible_rooms_in_workspace(
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
            workspace_id=_WORKSPACE_ID,
        )

    bound_logger.warning.assert_called_once()
    assert "not considered" in bound_logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_a_complete_read_says_nothing() -> None:
    repo = AsyncMock()
    repo.list_candidates = AsyncMock(return_value=([], False))
    bound_logger = MagicMock()

    with (
        patch.object(facade_mod, "ChatroomRepository", return_value=repo),
        patch.object(facade_mod, "logger", MagicMock(bind=MagicMock(return_value=bound_logger))),
    ):
        await facade_mod.ConversationFacade(AsyncMock()).visible_rooms_in_workspace(
            principal=SimpleNamespace(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
            workspace_id=_WORKSPACE_ID,
        )

    bound_logger.warning.assert_not_called()
