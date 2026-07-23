"""MinIO readiness — `HeadBucket` on every required bucket via the S3 API.

Exit criterion (B.5) spells "MinIO HeadBucket chat-uploads"; we satisfy it
with the SDK's `bucket_exists` which issues HeadBucket under the hood and
signs with SigV4 using the configured root credentials. We probe *all* buckets
`smap.bootstrap minio-init` provisions, not just chat-uploads: a MinIO that
predates a newer feature's bucket (e.g. knowmap-sources) is live and passes a
chat-uploads-only probe, yet every upload to the missing bucket 500s with
NoSuchBucket. Readiness gates on full provisioning, not just TCP liveness, so a
single missing bucket is "not ready" and the detail names which are absent.
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


def _required_buckets(settings: Settings) -> tuple[str, ...]:
    """The buckets `smap.bootstrap minio-init` provisions — kept in sync with it."""
    m = settings.minio
    return (
        m.bucket_chat_uploads,
        m.bucket_rag_sources,
        m.bucket_knowmap_sources,
        m.bucket_exports,
        m.bucket_agent_workspace,
        m.bucket_prompt_assistant_files,
        m.bucket_skill_bundles,
    )


async def probe_minio(settings: Settings) -> ProbeResult:
    required = _required_buckets(settings)

    def _check() -> tuple[bool, str | None]:
        client = _client(settings)
        missing = [b for b in required if not client.bucket_exists(b)]
        if missing:
            return False, f"missing buckets: {', '.join(missing)}"
        return True, None

    # minio-py is sync; run in a thread so we respect the 2-s budget. Seven fast
    # HeadBucket round-trips stay well inside 1.5 s on a healthy data plane.
    ok, detail = await asyncio.wait_for(asyncio.to_thread(_check), timeout=1.5)
    return ProbeResult("minio", ok, detail)
