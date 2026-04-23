"""MinIO-backed :class:`BlobStore` implementation for RAG sources.

Runs the blocking `minio` client in a thread executor so the async caller
isn't blocked on network I/O. Using the service account creds from Vault
(seeded by `smap.bootstrap minio-init`) is the responsibility of the app
factory — this class accepts a pre-built `Minio` client.
"""

from __future__ import annotations

import asyncio
import io
from typing import Final

from minio import Minio

from contexts.knowledge.application.ports import BlobStore

__all__ = ["MinioBlobStore"]


class MinioBlobStore(BlobStore):
    def __init__(self, client: Minio) -> None:
        self._client: Final[Minio] = client

    async def put(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
    ) -> str:
        def _op() -> None:
            self._client.put_object(
                bucket_name=bucket,
                object_name=key,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await asyncio.to_thread(_op)
        return f"{bucket}/{key}"

    async def get(self, *, bucket: str, key: str) -> bytes:
        def _op() -> bytes:
            resp = self._client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(_op)
