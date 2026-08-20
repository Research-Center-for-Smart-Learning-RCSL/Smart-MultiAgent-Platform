"""Enumeration follows confidentiality on the three listing routes (R13.32).

Before this, `GET /api/workspaces/{id}/chatrooms` returned every live room in the
workspace to anyone holding any role in the parent project, and for an org-owned
project every member of the org holds one (R5.03). Room names, all four access
flags and observer presence were disclosed for rooms the same caller was refused
on open. The project and workspace listings leaked the containers the same way.

These tests drive the route functions directly, as the rest of the route suite
does; the room-flag rule itself is covered by `test_visible_room_ids.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import chatrooms as chatrooms_mod
from app.api.v1 import projects as projects_mod
from app.api.v1 import workspaces as workspaces_mod
from app.api.v1.deps import PaginationParams
from contexts.conversation.domain.models import Chatroom
from contexts.tenancy.application.project_service import ProjectCandidates
from shared_kernel.auth.permissions import Role

_WORKSPACE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
_PROJECT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _principal(*, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


def _room(name: str) -> Chatroom:
    return Chatroom(
        id=uuid.uuid4(),
        workspace_id=_WORKSPACE_ID,
        name=name,
        allow_org_members=False,
        allow_project_members=True,
        allow_project_owners_only=False,
        allow_guest_links=False,
        guest_token="t",
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _pagination(*, limit: int = 100, offset: int = 0) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


async def _call_list_chatrooms(
    *,
    visible: list[Chatroom],
    roles: frozenset[Role] = frozenset({Role.PROJECT_MEMBER}),
    pagination: PaginationParams | None = None,
    is_admin: bool = False,
) -> tuple[list, AsyncMock]:
    facade = AsyncMock()
    facade.get_workspace = AsyncMock(return_value=SimpleNamespace(project_id=_PROJECT_ID))
    facade.visible_rooms_in_workspace = AsyncMock(return_value=visible)
    resolver = SimpleNamespace(roles_for=AsyncMock(return_value=roles))
    service = AsyncMock()
    service.rooms_with_observers = AsyncMock(return_value=set())

    with (
        patch.object(chatrooms_mod, "ConversationFacade", return_value=facade),
        patch.object(chatrooms_mod, "get_role_resolver", AsyncMock(return_value=resolver)),
        patch.object(chatrooms_mod, "ChatroomService", return_value=service),
    ):
        out = await chatrooms_mod.list_chatrooms(
            workspace_id=_WORKSPACE_ID,
            pagination=pagination or _pagination(),
            principal=_principal(is_admin=is_admin),
            db=AsyncMock(),
        )
    return out, facade


@pytest.mark.asyncio
async def test_listing_returns_only_rooms_the_room_acl_admits() -> None:
    """The route serves whatever the visibility filter returned, and nothing else."""
    visible = [_room("visible-a"), _room("visible-b")]

    out, facade = await _call_list_chatrooms(visible=visible)

    assert [r.id for r in out] == [r.id for r in visible]
    facade.visible_rooms_in_workspace.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_caller_with_no_visible_room_gets_an_empty_list_not_an_error() -> None:
    """A project member admitted by no room is a legitimate 200 with nothing in it."""
    out, _ = await _call_list_chatrooms(visible=[])
    assert out == []


@pytest.mark.asyncio
async def test_a_stranger_to_the_project_is_still_refused_outright() -> None:
    """R5.03 membership remains the price of admission; only the contents changed."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _call_list_chatrooms(visible=[_room("a")], roles=frozenset())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pagination_counts_visible_rooms_not_raw_rows() -> None:
    """AC-2: a full page of visible rooms is returned where enough exist."""
    visible = [_room(f"r{i}") for i in range(5)]

    page, _ = await _call_list_chatrooms(visible=visible, pagination=_pagination(limit=2, offset=0))
    assert [r.name for r in page] == ["r0", "r1"]

    page, _ = await _call_list_chatrooms(visible=visible, pagination=_pagination(limit=2, offset=2))
    assert [r.name for r in page] == ["r2", "r3"]

    page, _ = await _call_list_chatrooms(visible=visible, pagination=_pagination(limit=2, offset=4))
    assert [r.name for r in page] == ["r4"]


@pytest.mark.asyncio
async def test_offset_past_the_end_is_empty_not_an_error() -> None:
    page, _ = await _call_list_chatrooms(visible=[_room("only")], pagination=_pagination(limit=10, offset=50))
    assert page == []


@pytest.mark.asyncio
async def test_admin_skips_the_membership_probe_and_still_lists() -> None:
    visible = [_room("a")]
    facade = AsyncMock()
    facade.get_workspace = AsyncMock(return_value=SimpleNamespace(project_id=_PROJECT_ID))
    facade.visible_rooms_in_workspace = AsyncMock(return_value=visible)
    resolver = SimpleNamespace(roles_for=AsyncMock())
    service = AsyncMock()
    service.rooms_with_observers = AsyncMock(return_value=set())

    with (
        patch.object(chatrooms_mod, "ConversationFacade", return_value=facade),
        patch.object(chatrooms_mod, "get_role_resolver", AsyncMock(return_value=resolver)),
        patch.object(chatrooms_mod, "ChatroomService", return_value=service),
    ):
        out = await chatrooms_mod.list_chatrooms(
            workspace_id=_WORKSPACE_ID,
            pagination=_pagination(),
            principal=_principal(is_admin=True),
            db=AsyncMock(),
        )

    assert [r.id for r in out] == [r.id for r in visible]
    assert out[0].is_moderator is True
    resolver.roles_for.assert_not_called()


@pytest.mark.asyncio
async def test_moderator_bit_still_reflects_the_project_role() -> None:
    out, _ = await _call_list_chatrooms(visible=[_room("a")], roles=frozenset({Role.PROJECT_OWNER}))
    assert out[0].is_moderator is True

    out, _ = await _call_list_chatrooms(visible=[_room("a")], roles=frozenset({Role.ORG_MEMBER}))
    assert out[0].is_moderator is False


# --------------------------------------------------------------------------- #
# GET /api/projects/{id}/workspaces
# --------------------------------------------------------------------------- #


def _workspace(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=_PROJECT_ID,
        name=name,
        created_at=datetime.now(UTC),
        deleted_at=None,
        concept_map_enabled=False,
    )


async def _call_list_workspaces(
    *,
    all_workspaces: list[SimpleNamespace],
    with_visible_room: set[uuid.UUID],
    pagination: PaginationParams | None = None,
) -> list:
    service = AsyncMock()
    service.list_for_project = AsyncMock(return_value=all_workspaces)
    facade = AsyncMock()
    facade.workspace_ids_with_visible_room = AsyncMock(return_value=with_visible_room)

    with (
        patch.object(workspaces_mod, "WorkspaceService", return_value=service),
        patch.object(workspaces_mod, "ConversationFacade", return_value=facade),
    ):
        return await workspaces_mod.list_workspaces(
            project_id=_PROJECT_ID,
            pagination=pagination or _pagination(),
            principal=_principal(),
            db=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_workspace_listing_drops_workspaces_with_no_visible_room() -> None:
    mine, theirs = _workspace("mine"), _workspace("theirs")

    out = await _call_list_workspaces(
        all_workspaces=[mine, theirs],
        with_visible_room={mine.id},
    )

    assert [w.name for w in out] == ["mine"]


@pytest.mark.asyncio
async def test_workspace_listing_filters_before_paginating() -> None:
    workspaces = [_workspace(f"w{i}") for i in range(4)]
    visible = {workspaces[1].id, workspaces[3].id}

    out = await _call_list_workspaces(
        all_workspaces=workspaces,
        with_visible_room=visible,
        pagination=_pagination(limit=1, offset=1),
    )

    # Page 2 of the *filtered* list is w3, not w1 (which page 1 already served).
    assert [w.name for w in out] == ["w3"]


@pytest.mark.asyncio
async def test_workspace_listing_is_empty_not_an_error_when_nothing_is_visible() -> None:
    out = await _call_list_workspaces(
        all_workspaces=[_workspace("a"), _workspace("b")],
        with_visible_room=set(),
    )
    assert out == []


# --------------------------------------------------------------------------- #
# GET /api/projects
# --------------------------------------------------------------------------- #


def _project(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        owner_org_id=uuid.uuid4(),
        owner_user_id=None,
        created_by_user_id=uuid.uuid4(),
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


async def _call_list_projects(
    *,
    candidates: list[SimpleNamespace],
    directly_visible: set[uuid.UUID],
    with_visible_room: set[uuid.UUID],
) -> tuple[list, AsyncMock]:
    service = AsyncMock()
    service.list_candidates_for_user = AsyncMock(
        return_value=ProjectCandidates(projects=candidates, directly_visible_ids=directly_visible)
    )
    facade = AsyncMock()
    facade.project_ids_with_visible_room = AsyncMock(return_value=with_visible_room)

    with (
        patch.object(projects_mod, "ProjectService", return_value=service),
        patch.object(projects_mod, "ConversationFacade", return_value=facade),
    ):
        out = await projects_mod.list_projects(
            scope=None,
            owner_id=None,
            pagination=_pagination(),
            principal=_principal(),
            db=AsyncMock(),
        )
    return out, facade


@pytest.mark.asyncio
async def test_org_member_no_longer_sees_a_sibling_project_with_no_visible_room() -> None:
    """The defect: any org member could enumerate every project of the org."""
    mine, theirs = _project("mine"), _project("theirs")

    out, _ = await _call_list_projects(
        candidates=[mine, theirs],
        directly_visible={mine.id},
        with_visible_room=set(),
    )

    assert [p.name for p in out] == ["mine"]


@pytest.mark.asyncio
async def test_a_project_reachable_only_through_an_org_open_room_is_still_listed() -> None:
    """Otherwise `allow_org_members` rooms would have no entry point at all."""
    mine, open_to_org = _project("mine"), _project("open-to-org")

    out, _ = await _call_list_projects(
        candidates=[mine, open_to_org],
        directly_visible={mine.id},
        with_visible_room={open_to_org.id},
    )

    assert sorted(p.name for p in out) == ["mine", "open-to-org"]


@pytest.mark.asyncio
async def test_a_members_own_project_is_listed_even_with_no_readable_room() -> None:
    """Standing in the project settles it; the room test is only for the rest."""
    mine = _project("mine")

    out, facade = await _call_list_projects(
        candidates=[mine],
        directly_visible={mine.id},
        with_visible_room=set(),
    )

    assert [p.name for p in out] == ["mine"]
    # Nothing was undecided, so the room query is never issued.
    facade.project_ids_with_visible_room.assert_not_called()


@pytest.mark.asyncio
async def test_only_undecided_projects_are_sent_to_the_room_query() -> None:
    mine, theirs = _project("mine"), _project("theirs")

    _, facade = await _call_list_projects(
        candidates=[mine, theirs],
        directly_visible={mine.id},
        with_visible_room=set(),
    )

    facade.project_ids_with_visible_room.assert_awaited_once()
    assert facade.project_ids_with_visible_room.await_args.kwargs["project_ids"] == [theirs.id]
