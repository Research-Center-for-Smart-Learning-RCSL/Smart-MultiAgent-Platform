"""Unit tests for workspace-scoped dashboard resolution ([R33.01]-[R33.02])."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.dashboard import _resolve_workspace_rooms
from shared_kernel.auth.permissions import Principal


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _principal(user_id: uuid.UUID | None = None) -> Principal:
    return Principal(
        user_id=user_id or _uid(),
        is_admin=False,
        email_verified=True,
    )


@dataclass(frozen=True)
class FakeWorkspace:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str = "Test WS"
    created_at: dt.datetime = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    deleted_at: dt.datetime | None = None
    concept_map_enabled: bool = False


class TestResolveWorkspaceRooms:
    """AC-1 through AC-5: workspace room resolution."""

    @pytest.mark.asyncio
    async def test_returns_only_visible_rooms_in_workspace(self) -> None:
        """R13.32: a member cannot discover rooms the room ACL hides."""
        ws_id = _uid()
        proj_id = _uid()
        r1 = _uid()
        ws = FakeWorkspace(id=ws_id, project_id=proj_id)

        @dataclass(frozen=True)
        class FakeRoom:
            id: uuid.UUID
            name: str

        db = AsyncMock()
        principal = _principal()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.assert_project_role") as assert_pm,
            patch("app.api.v1.dashboard.TenancyFacade") as TenancyCls,
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)
            facade.visible_rooms_in_workspace = AsyncMock(return_value=[FakeRoom(r1, "Room 1")])
            TenancyCls.return_value.get_project = AsyncMock(return_value=object())
            assert_pm.return_value = None

            project_id, result = await _resolve_workspace_rooms(
                db=db, principal=principal, workspace_id=ws_id
            )

        assert project_id == proj_id
        assert len(result) == 1
        assert result[r1] == "Room 1"
        assert_pm.assert_awaited_once_with(db=db, principal=principal, project_id=proj_id)
        facade.visible_rooms_in_workspace.assert_awaited_once_with(
            principal=principal,
            workspace_id=ws_id,
        )

    @pytest.mark.asyncio
    async def test_404_when_workspace_not_found(self) -> None:
        """AC-4: missing workspace returns 404."""
        db = AsyncMock()
        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.TenancyFacade"),
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(db=db, principal=_principal(), workspace_id=_uid())
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_workspace_deleted(self) -> None:
        """AC-4: soft-deleted workspace returns 404."""
        ws = FakeWorkspace(
            id=_uid(),
            project_id=_uid(),
            deleted_at=dt.datetime.now(dt.UTC),
        )
        db = AsyncMock()
        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.TenancyFacade"),
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(db=db, principal=_principal(), workspace_id=ws.id)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_404_when_workspace_project_is_deleted(self) -> None:
        ws = FakeWorkspace(id=_uid(), project_id=_uid())
        db = AsyncMock()
        principal = _principal()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.TenancyFacade") as TenancyCls,
            patch("app.api.v1.dashboard.assert_project_role") as assert_role,
        ):
            FacadeCls.return_value.get_workspace = AsyncMock(return_value=ws)
            TenancyCls.return_value.get_project = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(db=db, principal=principal, workspace_id=ws.id)

        assert exc_info.value.status_code == 404
        assert_role.assert_not_awaited()
        FacadeCls.return_value.visible_rooms_in_workspace.assert_not_called()

    @pytest.mark.asyncio
    async def test_403_when_not_project_member(self) -> None:
        """AC-5: non-member gets 403."""
        ws = FakeWorkspace(id=_uid(), project_id=_uid())
        db = AsyncMock()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch(
                "app.api.v1.dashboard.assert_project_role",
                side_effect=HTTPException(status_code=403, detail="Not a member"),
            ),
            patch("app.api.v1.dashboard.TenancyFacade") as TenancyCls,
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)
            TenancyCls.return_value.get_project = AsyncMock(return_value=object())

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(db=db, principal=_principal(), workspace_id=ws.id)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_when_no_rooms(self) -> None:
        """Returns empty dict when workspace has no chatrooms."""
        ws = FakeWorkspace(id=_uid(), project_id=_uid())
        db = AsyncMock()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.assert_project_role"),
            patch("app.api.v1.dashboard.TenancyFacade") as TenancyCls,
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)
            TenancyCls.return_value.get_project = AsyncMock(return_value=object())
            facade.visible_rooms_in_workspace = AsyncMock(return_value=[])

            result = await _resolve_workspace_rooms(db=db, principal=_principal(), workspace_id=ws.id)

        assert result == (ws.project_id, {})
