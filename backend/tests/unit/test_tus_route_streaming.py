"""The TUS PATCH route bounds chunked request bodies while streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.api.v1.tus import _read_patch_chunk
from contexts.conversation.application.tus_service import TUS_MAX_CHUNK
from contexts.conversation.domain.errors import AttachmentTooLarge


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
