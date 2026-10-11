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
import hashlib
import hmac
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import files as files_route
from shared_kernel.errors.handlers import register_exception_handlers
from shared_kernel.storage import file_tickets
from shared_kernel.storage.minio_client import MinioClient, get_minio_client, reset_for_tests

pytestmark = pytest.mark.wiring


class _Signer:
    key = b"w" * 32

    def hmac_sign(self, data: bytes) -> tuple[int, bytes]:
        return 1, hmac.new(self.key, data, hashlib.sha256).digest()

    def hmac_verify(self, version: int, data: bytes, mac: bytes) -> bool:
        return version == 1 and hmac.compare_digest(self.hmac_sign(data)[1], mac)


async def test_ticket_streams_the_stored_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_tickets, "_signer", _Signer)
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
    assert missing.status_code == 404


async def _ensure_bucket(minio: MinioClient, bucket: str) -> None:
    # A fresh test stack has not run smap.bootstrap minio-init.
    client = minio._client

    def _make() -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    await asyncio.to_thread(_make)
