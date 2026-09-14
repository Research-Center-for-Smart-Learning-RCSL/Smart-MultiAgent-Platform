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
    async def test_returns_all_rooms_in_workspace(self) -> None:
        """AC-1: all rooms returned regardless of creator."""
        ws_id = _uid()
        proj_id = _uid()
        r1, r2 = _uid(), _uid()
        ws = FakeWorkspace(id=ws_id, project_id=proj_id)

        @dataclass(frozen=True)
        class FakeRoom:
            name: str
            created_by_user_id: uuid.UUID

        db = AsyncMock()
        principal = _principal()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.assert_project_membership") as assert_pm,
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)
            facade.list_chatroom_ids_for_workspace = AsyncMock(return_value=[r1, r2])
            facade.get_chatrooms = AsyncMock(
                return_value={
                    r1: FakeRoom(name="Room 1", created_by_user_id=_uid()),
                    r2: FakeRoom(name="Room 2", created_by_user_id=_uid()),
                }
            )
            assert_pm.return_value = None

            result = await _resolve_workspace_rooms(
                db=db, principal=principal, workspace_id=ws_id
            )

        assert len(result) == 2
        assert result[r1] == "Room 1"
        assert result[r2] == "Room 2"
        assert_pm.assert_awaited_once_with(db=db, principal=principal, project_id=proj_id)

    @pytest.mark.asyncio
    async def test_404_when_workspace_not_found(self) -> None:
        """AC-4: missing workspace returns 404."""
        db = AsyncMock()
        with patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls:
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(
                    db=db, principal=_principal(), workspace_id=_uid()
                )
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
        with patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls:
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(
                    db=db, principal=_principal(), workspace_id=ws.id
                )
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_403_when_not_project_member(self) -> None:
        """AC-5: non-member gets 403."""
        ws = FakeWorkspace(id=_uid(), project_id=_uid())
        db = AsyncMock()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch(
                "app.api.v1.dashboard.assert_project_membership",
                side_effect=HTTPException(status_code=403, detail="Not a member"),
            ),
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)

            with pytest.raises(HTTPException) as exc_info:
                await _resolve_workspace_rooms(
                    db=db, principal=_principal(), workspace_id=ws.id
                )
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_when_no_rooms(self) -> None:
        """Returns empty dict when workspace has no chatrooms."""
        ws = FakeWorkspace(id=_uid(), project_id=_uid())
        db = AsyncMock()

        with (
            patch("app.api.v1.dashboard.ConversationFacade") as FacadeCls,
            patch("app.api.v1.dashboard.assert_project_membership"),
        ):
            facade = FacadeCls.return_value
            facade.get_workspace = AsyncMock(return_value=ws)
            facade.list_chatroom_ids_for_workspace = AsyncMock(return_value=[])

            result = await _resolve_workspace_rooms(
                db=db, principal=_principal(), workspace_id=ws.id
            )

        assert result == {}
