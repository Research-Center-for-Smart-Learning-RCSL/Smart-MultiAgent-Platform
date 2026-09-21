"""Research data export route tests (AC-1, AC-2, AC-10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.research_export import (
    ResearchExportCreateIn,
    ResearchExportCreateOut,
    ResearchExportStatusOut,
    _resolve_window,
    create_research_export,
    get_research_export,
)
from contexts.conversation.application.research_export_service import (
    ResearchExportJobState,
    ResearchExportJobStatus,
)

_WORKSPACE = uuid.uuid4()
_PROJECT = uuid.uuid4()
_USER = uuid.uuid4()
_JOB = uuid.uuid4()


def _make_workspace(project_id: uuid.UUID = _PROJECT) -> MagicMock:
    ws = MagicMock()
    ws.project_id = project_id
    ws.deleted_at = None
    return ws


def _make_principal(user_id: uuid.UUID = _USER, is_admin: bool = False) -> MagicMock:
    p = MagicMock()
    p.user_id = user_id
    p.is_admin = is_admin
    return p


def _make_state(
    status: str = ResearchExportJobStatus.QUEUED,
) -> ResearchExportJobState:
    return ResearchExportJobState(
        job_id=_JOB,
        workspace_id=_WORKSPACE,
        owner_user_id=_USER,
        status=status,
        created_at=datetime(2026, 9, 21, 12, 0, 0),
    )


class TestCreateResearchExport:
    @pytest.mark.asyncio
    async def test_returns_202_with_job_id(self) -> None:
        db = AsyncMock()
        ctx = MagicMock()
        principal = _make_principal()

        facade_inst = AsyncMock()
        facade_inst.get_workspace.return_value = _make_workspace()

        with (
            patch(
                "contexts.conversation.interfaces.facade.ConversationFacade",
                return_value=facade_inst,
            ),
            patch(
                "app.api.v1.deps.assert_project_owner",
                new_callable=AsyncMock,
            ) as mock_owner,
            patch(
                "contexts.conversation.application.research_export_service.create",
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                "shared_kernel.queue.enqueue",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            mock_owner.return_value = None
            state = _make_state()
            mock_create.return_value = state
            mock_enqueue.return_value = None

            result = await create_research_export(
                workspace_id=_WORKSPACE,
                body=None,
                ctx=ctx,
                principal=principal,
                db=db,
            )

            assert isinstance(result, ResearchExportCreateOut)
            assert result.job_id == _JOB
            assert result.status == ResearchExportJobStatus.QUEUED
            mock_enqueue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_403_for_non_owner(self) -> None:
        from fastapi import HTTPException

        db = AsyncMock()
        ctx = MagicMock()
        principal = _make_principal()

        facade_inst = AsyncMock()
        facade_inst.get_workspace.return_value = _make_workspace()

        with (
            patch(
                "contexts.conversation.interfaces.facade.ConversationFacade",
                return_value=facade_inst,
            ),
            patch(
                "app.api.v1.deps.assert_project_owner",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=403, detail="forbidden"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_research_export(
                    workspace_id=_WORKSPACE,
                    body=None,
                    ctx=ctx,
                    principal=principal,
                    db=db,
                )
            assert exc_info.value.status_code == 403


class TestGetResearchExport:
    @pytest.mark.asyncio
    async def test_returns_ready_with_url(self) -> None:
        state = ResearchExportJobState(
            job_id=_JOB,
            workspace_id=_WORKSPACE,
            owner_user_id=_USER,
            status=ResearchExportJobStatus.READY,
            created_at=datetime(2026, 9, 21, 12, 0, 0),
            bucket="exports",
            object_key="research/test/file.zip",
        )
        principal = _make_principal()

        with (
            patch(
                "contexts.conversation.application.research_export_service.get",
                new_callable=AsyncMock,
                return_value=state,
            ),
            patch(
                "app.api.v1.research_export.get_minio_client",
            ) as mock_minio,
        ):
            client = AsyncMock()
            client.presigned_get.return_value = "https://minio.local/signed-url"
            mock_minio.return_value = client

            result = await get_research_export(job_id=_JOB, principal=principal)
            assert isinstance(result, ResearchExportStatusOut)
            assert result.status == ResearchExportJobStatus.READY
            assert result.url == "https://minio.local/signed-url"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_job(self) -> None:
        from contexts.conversation.domain.errors import ExportJobNotFound

        principal = _make_principal()

        with patch(
            "contexts.conversation.application.research_export_service.get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ExportJobNotFound):
                await get_research_export(job_id=_JOB, principal=principal)


class TestResolveWindow:
    def test_none_dates_return_none(self) -> None:
        body = ResearchExportCreateIn()
        after, before = _resolve_window(body)
        assert after is None
        assert before is None

    def test_dates_resolve_to_utc_bounds(self) -> None:
        from datetime import date

        body = ResearchExportCreateIn(
            created_after=date(2026, 1, 1),
            created_before=date(2026, 1, 31),
        )
        after, before = _resolve_window(body)
        assert after is not None
        assert before is not None
        assert after.day == 1
        assert before.day == 1
        assert before.month == 2
