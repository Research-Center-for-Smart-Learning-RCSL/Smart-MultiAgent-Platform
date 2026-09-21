"""Research export job state tests (AC-8, AC-9)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application.research_export_service import (
    _JOB_TTL_SECONDS,
    ResearchExportJobState,
    ResearchExportJobStatus,
    create,
    get,
    mark_ready,
    mark_running,
)

_WORKSPACE = uuid.uuid4()
_USER = uuid.uuid4()


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get.return_value = None
    redis.set.return_value = None
    return redis


class TestCreateJobState:
    @pytest.mark.asyncio
    async def test_creates_queued_state(self, mock_redis: AsyncMock) -> None:
        with patch(
            "contexts.conversation.application.research_export_service.get_redis",
            return_value=mock_redis,
        ):
            state = await create(
                workspace_id=_WORKSPACE,
                owner_user_id=_USER,
            )
            assert state.status == ResearchExportJobStatus.QUEUED
            assert state.workspace_id == _WORKSPACE
            assert state.owner_user_id == _USER
            mock_redis.set.assert_awaited_once()
            call_args = mock_redis.set.call_args
            assert call_args.kwargs["ex"] == _JOB_TTL_SECONDS


class TestJobTtl:
    def test_ttl_is_72_hours(self) -> None:
        assert _JOB_TTL_SECONDS == 72 * 3600


class TestMarkTransitions:
    @pytest.mark.asyncio
    async def test_mark_running(self, mock_redis: AsyncMock) -> None:
        state = ResearchExportJobState(
            job_id=uuid.uuid4(),
            workspace_id=_WORKSPACE,
            owner_user_id=_USER,
            status=ResearchExportJobStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        stored = json.dumps(
            {
                "job_id": str(state.job_id),
                "workspace_id": str(state.workspace_id),
                "owner_user_id": str(state.owner_user_id),
                "status": state.status,
                "created_at": state.created_at.isoformat(),
                "object_key": None,
                "bucket": None,
                "created_after": None,
                "created_before": None,
                "error": None,
            }
        )
        mock_redis.get.return_value = stored

        with patch(
            "contexts.conversation.application.research_export_service.get_redis",
            return_value=mock_redis,
        ):
            await mark_running(state.job_id)
            set_call = mock_redis.set.call_args
            payload = json.loads(set_call.args[1])
            assert payload["status"] == ResearchExportJobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_mark_ready_stores_bucket_and_key(self, mock_redis: AsyncMock) -> None:
        jid = uuid.uuid4()
        state_dict = {
            "job_id": str(jid),
            "workspace_id": str(_WORKSPACE),
            "owner_user_id": str(_USER),
            "status": ResearchExportJobStatus.RUNNING,
            "created_at": datetime.now(UTC).isoformat(),
            "object_key": None,
            "bucket": None,
            "created_after": None,
            "created_before": None,
            "error": None,
        }
        mock_redis.get.return_value = json.dumps(state_dict)

        with patch(
            "contexts.conversation.application.research_export_service.get_redis",
            return_value=mock_redis,
        ):
            await mark_ready(job_id=jid, bucket="exports", object_key="research/x/y.zip")
            set_call = mock_redis.set.call_args
            payload = json.loads(set_call.args[1])
            assert payload["status"] == ResearchExportJobStatus.READY
            assert payload["bucket"] == "exports"
            assert payload["object_key"] == "research/x/y.zip"


class TestGetJobState:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, mock_redis: AsyncMock) -> None:
        with patch(
            "contexts.conversation.application.research_export_service.get_redis",
            return_value=mock_redis,
        ):
            result = await get(uuid.uuid4())
            assert result is None

    @pytest.mark.asyncio
    async def test_deserializes_stored_state(self, mock_redis: AsyncMock) -> None:
        jid = uuid.uuid4()
        state_dict = {
            "job_id": str(jid),
            "workspace_id": str(_WORKSPACE),
            "owner_user_id": str(_USER),
            "status": ResearchExportJobStatus.READY,
            "created_at": "2026-09-21T10:00:00",
            "object_key": "research/test.zip",
            "bucket": "exports",
            "created_after": "2026-01-01T00:00:00",
            "created_before": None,
            "error": None,
        }
        mock_redis.get.return_value = json.dumps(state_dict)

        with patch(
            "contexts.conversation.application.research_export_service.get_redis",
            return_value=mock_redis,
        ):
            result = await get(jid)
            assert result is not None
            assert result.job_id == jid
            assert result.status == ResearchExportJobStatus.READY
            assert result.object_key == "research/test.zip"
            assert result.created_after is not None
            assert result.created_before is None
