"""MinIO readiness — `HeadBucket chat-uploads` against the data-plane S3 API.

Exit criterion (B.5) spells "MinIO HeadBucket chat-uploads"; we satisfy it
with the SDK's `bucket_exists` which issues HeadBucket under the hood and
signs with SigV4 using the configured root credentials. A missing bucket
before `smap.bootstrap minio-init` has run counts as "not ready" — that is
correct semantics: readiness gates on full provisioning, not just TCP liveness.
"""

from __future__ import annotations

import asyncio

from minio import Minio

from app.config.settings import Settings

from .base import ProbeResult


def _client(settings: Settings) -> Minio:
    return Minio(
        settings.minio.endpoint,
        access_key=settings.minio.root_access_key,
        secret_key=settings.minio.root_secret_key,
        secure=settings.minio.use_tls,
        region=settings.minio.region,
    )


async def probe_minio(settings: Settings) -> ProbeResult:
    def _check() -> tuple[bool, str | None]:
        client = _client(settings)
        exists = client.bucket_exists(settings.minio.bucket_chat_uploads)
        return exists, None if exists else "chat-uploads bucket missing"

    # minio-py is sync; run in a thread so we respect the 2-s budget.
    ok, detail = await asyncio.wait_for(asyncio.to_thread(_check), timeout=1.5)
    return ProbeResult("minio", ok, detail)
