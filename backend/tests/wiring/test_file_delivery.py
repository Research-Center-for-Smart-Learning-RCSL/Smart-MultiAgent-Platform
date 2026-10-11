"""Browser file delivery against a real MinIO and Redis ([R19a.14]).

Every unit test of the old delivery path mocked MinIO and asserted the URL it
returned, so a URL no browser could reach shipped green
(docs/audits/2026-10-11-chat-and-research-export/findings.md F-1). This test
fetches the bytes through the app the way a browser does.

Requires the compose test data plane (MinIO + Redis), like the rest of the
wiring tier. The Vault HMAC seed is replaced by a local signer: what is under
test is storage and HTTP, not the key store.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import files as files_route
from shared_kernel.errors.handlers import register_exception_handlers
from shared_kernel.storage import file_tickets
from shared_kernel.storage.minio_client import MinioClient, get_minio_client, reset_for_tests
from tests.unit.file_ticket_support import FakeSigner

pytestmark = pytest.mark.wiring


async def test_ticket_streams_the_stored_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_tickets, "_signer", FakeSigner)
    reset_for_tests()
    minio = get_minio_client()
    bucket = minio.exports_bucket
    await _ensure_bucket(minio, bucket)
    key = f"wiring/{uuid.uuid4()}/archive.bin"
    body = os.urandom(2 * 1024 * 1024 + 123)  # spans several stream chunks
    await minio.put_object(bucket=bucket, key=key, data=body, content_type="application/zip")

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(files_route.router)
    path = await file_tickets.issue(
        bucket=bucket,
        key=key,
        content_type="application/zip",
        disposition=file_tickets.content_disposition("attachment", "課堂紀錄.zip"),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            got = await client.get(path)
            head = await client.head(path)
            # A resumed download: MinIO must honour the offset, not just the route.
            resumed = await client.get(path, headers={"Range": f"bytes={len(body) - 1000}-"})
            missing = await client.get(
                await file_tickets.issue(
                    bucket=bucket, key=f"{key}.gone", content_type="application/zip", disposition="attachment"
                )
            )
    finally:
        await minio.remove(bucket=bucket, key=key)

    assert got.status_code == 200
    assert got.content == body
    assert got.headers["content-length"] == str(len(body))
    assert got.headers["content-disposition"].startswith('attachment; filename="download.zip"')
    assert got.headers["content-security-policy"].startswith("sandbox")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(body))
    assert resumed.status_code == 206
    assert resumed.content == body[-1000:]
    assert missing.status_code == 404


async def _ensure_bucket(minio: MinioClient, bucket: str) -> None:
    # A fresh test stack has not run smap.bootstrap minio-init.
    client = minio._client

    def _make() -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    await asyncio.to_thread(_make)


async def test_stream_slots_cap_release_and_reclaim() -> None:
    """The per-ticket concurrency cap against real Redis (FU-5)."""
    from shared_kernel.auth import ratelimit

    key = f"files:streams:wiring-{uuid.uuid4()}"
    a = await ratelimit.acquire_slot(key=key, max_count=2, hold_sec=60)
    b = await ratelimit.acquire_slot(key=key, max_count=2, hold_sec=60)
    assert a is not None
    assert b is not None
    assert await ratelimit.acquire_slot(key=key, max_count=2, hold_sec=60) is None

    await ratelimit.release_slot(key=key, token=a)
    assert await ratelimit.acquire_slot(key=key, max_count=2, hold_sec=60) is not None

    # A holder whose release never ran is reclaimed once its hold window passes.
    leaked = f"{key}:leak"
    assert await ratelimit.acquire_slot(key=leaked, max_count=1, hold_sec=1) is not None
    assert await ratelimit.acquire_slot(key=leaked, max_count=1, hold_sec=1) is None
    await asyncio.sleep(1.2)
    assert await ratelimit.acquire_slot(key=leaked, max_count=1, hold_sec=1) is not None
