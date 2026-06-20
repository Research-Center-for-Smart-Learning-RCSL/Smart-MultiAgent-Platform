"""Async facade over the synchronous `minio` SDK.

The `minio` library is a thin HTTP client that does blocking socket I/O. We
wrap its calls in `asyncio.to_thread` so async handlers (TUS PATCH, export
worker, attachment fetch) never block the event loop. One process-wide
client is cached — the SDK is itself threadsafe for reads, and Minio
servers do not require a session keepalive object.
"""

from __future__ import annotations

import asyncio
import io
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from minio import Minio
from minio.error import S3Error

from app.config.settings import MinioSection, get_settings

_lock: Final = threading.Lock()
_instance: MinioClient | None = None


class StorageError(RuntimeError):
    """Raised for any MinIO call that fails — handlers map it to 503/500."""


@dataclass(frozen=True, slots=True)
class ObjectStat:
    size: int
    etag: str
    content_type: str | None


class MinioClient:
    def __init__(self, section: MinioSection) -> None:
        self._cfg = section
        self._client = Minio(
            section.endpoint,
            access_key=section.root_access_key,
            secret_key=section.root_secret_key,
            secure=section.use_tls,
            region=section.region,
        )

    # ---- buckets ---------------------------------------------------------

    @property
    def chat_uploads_bucket(self) -> str:
        return self._cfg.bucket_chat_uploads

    @property
    def rag_sources_bucket(self) -> str:
        return self._cfg.bucket_rag_sources

    @property
    def exports_bucket(self) -> str:
        return self._cfg.bucket_exports

    # ---- operations ------------------------------------------------------

    async def put_object(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        def _put() -> None:
            try:
                self._client.put_object(
                    bucket,
                    key,
                    data=io.BytesIO(data),
                    length=len(data),
                    content_type=content_type,
                )
            except S3Error as exc:  # pragma: no cover — network-dependent
                raise StorageError(f"put_object failed: {exc}") from exc

        await asyncio.to_thread(_put)

    async def put_file(
        self,
        *,
        bucket: str,
        key: str,
        file_path: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        def _put() -> None:
            try:
                self._client.fput_object(
                    bucket,
                    key,
                    file_path,
                    content_type=content_type,
                )
            except S3Error as exc:  # pragma: no cover
                raise StorageError(f"fput_object failed: {exc}") from exc

        await asyncio.to_thread(_put)

    async def get_object(self, *, bucket: str, key: str) -> bytes:
        def _get() -> bytes:
            resp = None
            try:
                resp = self._client.get_object(bucket, key)
                return resp.read()
            except S3Error as exc:
                raise StorageError(f"get_object failed: {exc}") from exc
            finally:
                if resp is not None:
                    resp.close()
                    resp.release_conn()

        return await asyncio.to_thread(_get)

    async def stat(self, *, bucket: str, key: str) -> ObjectStat | None:
        def _stat() -> ObjectStat | None:
            try:
                obj = self._client.stat_object(bucket, key)
            except S3Error as exc:
                if exc.code in ("NoSuchKey", "NoSuchBucket"):
                    return None
                raise StorageError(f"stat_object failed: {exc}") from exc
            return ObjectStat(
                size=obj.size or 0,
                etag=(obj.etag or "").strip('"'),
                content_type=obj.content_type,
            )

        return await asyncio.to_thread(_stat)

    async def remove(self, *, bucket: str, key: str) -> None:
        def _rm() -> None:
            try:
                self._client.remove_object(bucket, key)
            except S3Error as exc:  # pragma: no cover
                # Idempotent delete — ignore NoSuchKey.
                if exc.code not in ("NoSuchKey",):
                    raise StorageError(f"remove_object failed: {exc}") from exc

        await asyncio.to_thread(_rm)

    def list_objects_sync(self, bucket: str) -> list[Any]:
        """Return all objects in *bucket* (recursive). Caller runs in a thread."""
        try:
            return list(self._client.list_objects(bucket, recursive=True))
        except S3Error as exc:
            raise StorageError(f"list_objects failed: {exc}") from exc

    def remove_object_sync(self, bucket: str, key: str) -> None:
        """Remove one object; idempotent on NoSuchKey. Caller runs in a thread."""
        try:
            self._client.remove_object(bucket, key)
        except S3Error as exc:
            if exc.code not in ("NoSuchKey",):
                raise StorageError(f"remove_object failed: {exc}") from exc

    async def presigned_get(
        self,
        *,
        bucket: str,
        key: str,
        expires: timedelta = timedelta(minutes=15),
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        # Response-header overrides ride in the presigned URL's signed query
        # string, so the object's *stored* Content-Type (which is
        # attacker-controlled for chat uploads) is overridden at serve time —
        # the browser sees what we dictate, not what the uploader declared
        # (SEC-M2). MinIO honours `response-content-type` /
        # `response-content-disposition` exactly like S3.
        # Widened to the minio stub's value type (str | list[str] | tuple[str]);
        # we only ever store str values but the SDK signature is broader.
        response_headers: dict[str, str | list[str] | tuple[str]] = {}
        if response_content_type is not None:
            response_headers["response-content-type"] = response_content_type
        if response_content_disposition is not None:
            response_headers["response-content-disposition"] = response_content_disposition

        def _sign() -> str:
            try:
                return self._client.presigned_get_object(
                    bucket,
                    key,
                    expires=expires,
                    response_headers=response_headers or None,
                )
            except S3Error as exc:  # pragma: no cover
                raise StorageError(f"presigned_get failed: {exc}") from exc

        return await asyncio.to_thread(_sign)


def get_minio_client() -> MinioClient:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = MinioClient(get_settings().minio)
    return _instance


def reset_for_tests() -> None:
    global _instance
    _instance = None


# --------------------------------------------------------------------------- #
# Key builders — §21.5 layout. Keeping these in one module means every
# writer (single-shot attachment, TUS finaliser, export worker) agrees on the
# object path without importing from each other.
# --------------------------------------------------------------------------- #


def chat_upload_key(
    *,
    project_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    attachment_id: uuid.UUID,
    filename: str,
) -> str:
    # `attachment_id` stands in for `{msg_id}` until the attachment is bound to
    # a message at send-time — the object path then becomes addressable by the
    # attachment record regardless of message existence. Filename is appended
    # for operator legibility and is already length-capped at the route layer.
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"{project_id}/{chatroom_id}/{attachment_id}/{safe_name}"


def rag_source_key(*, project_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"{project_id}/{document_id}/{safe_name}"


def export_key(*, job_id: uuid.UUID, filename: str) -> str:
    # SEC-L6: sanitise like chat_upload_key / rag_source_key so a filename can
    # never inject path separators into the object key. Keys are UUID-namespaced
    # so this is consistency / defence-in-depth, not a live traversal fix.
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"{job_id}/{safe_name}"


__all__ = [
    "MinioClient",
    "ObjectStat",
    "StorageError",
    "chat_upload_key",
    "export_key",
    "get_minio_client",
    "rag_source_key",
    "reset_for_tests",
]
