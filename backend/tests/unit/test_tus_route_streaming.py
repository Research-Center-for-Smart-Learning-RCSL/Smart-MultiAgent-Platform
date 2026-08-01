"""The TUS PATCH route bounds chunked request bodies while streaming."""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.tus import _read_patch_chunk, _reauthorize_completion
from contexts.conversation.application.tus_service import TUS_MAX_CHUNK
from contexts.conversation.domain.errors import AttachmentTooLarge
from shared_kernel.auth.permissions import Principal


class _StreamingRequest:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def body(self) -> bytes:
        raise AssertionError("request.body() must not be used for TUS PATCH")


@pytest.mark.asyncio
async def test_chunked_patch_accepts_exactly_the_16_mib_limit() -> None:
    request = _StreamingRequest([b"a" * (8 * 1024 * 1024), b"b" * (8 * 1024 * 1024)])

    body = await _read_patch_chunk(request)  # type: ignore[arg-type]

    assert len(body) == TUS_MAX_CHUNK


@pytest.mark.asyncio
async def test_chunked_patch_rejects_limit_plus_one_without_calling_body() -> None:
    request = _StreamingRequest([b"a" * TUS_MAX_CHUNK, b"x"])

    with pytest.raises(AttachmentTooLarge, match="exceeds 16 MB cap"):
        await _read_patch_chunk(request)  # type: ignore[arg-type]


def _metadata(**values: str) -> str:
    return ",".join(f"{key} {base64.b64encode(value.encode()).decode()}" for key, value in values.items())


@pytest.mark.asyncio
async def test_rag_completion_rechecks_current_owner_and_allowlist() -> None:
    config_id = uuid.uuid4()
    project_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)
    facade = SimpleNamespace(
        get_rag_config=AsyncMock(
            return_value=SimpleNamespace(
                id=config_id,
                project_id=project_id,
                embed_key_id=uuid.uuid4(),
            )
        )
    )
    owner_check = AsyncMock()
    allowlist_check = AsyncMock()

    with (
        patch("app.api.v1.tus.KnowledgeFacade", return_value=facade),
        patch("app.api.v1.tus._require_rag_owner", owner_check),
        patch("app.api.v1.rag.validate_agent_allowlist", allowlist_check),
    ):
        await _reauthorize_completion(
            AsyncMock(),
            principal=principal,
            metadata_raw=_metadata(
                purpose="rag_source",
                rag_config_id=str(config_id),
                rag_agent_ids=str(agent_id),
            ),
        )

    owner_check.assert_awaited_once()
    allowlist_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_rag_completion_rejects_revoked_owner() -> None:
    config_id = uuid.uuid4()
    principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)
    facade = SimpleNamespace(
        get_rag_config=AsyncMock(
            return_value=SimpleNamespace(
                id=config_id,
                project_id=uuid.uuid4(),
                embed_key_id=uuid.uuid4(),
            )
        )
    )

    with (
        patch("app.api.v1.tus.KnowledgeFacade", return_value=facade),
        patch(
            "app.api.v1.tus._require_rag_owner",
            AsyncMock(side_effect=HTTPException(status_code=403)),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _reauthorize_completion(
            AsyncMock(),
            principal=principal,
            metadata_raw=_metadata(
                purpose="rag_source",
                rag_config_id=str(config_id),
            ),
        )

    assert exc_info.value.status_code == 403
