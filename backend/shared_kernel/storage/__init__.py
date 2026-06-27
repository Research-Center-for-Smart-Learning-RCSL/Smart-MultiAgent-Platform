"""Object-storage surface (§21.5).

Two concerns live here:

- `MinioClient` — a thin async facade over the sync `minio` SDK that every
  bounded context calls. The SDK is sync (network I/O on the calling thread),
  so each call is wrapped with `asyncio.to_thread` to keep the FastAPI event
  loop free.
- Bucket constants + key builders — so the `{project_id}/{chatroom_id}/...`
  layout is defined once and never hand-rolled in a service.

Auth: reads the runtime service-account creds Vault provisioned at bootstrap
(fallback to the Minio section creds in dev).
"""

from __future__ import annotations

from shared_kernel.storage.minio_client import (
    MinioClient,
    StorageError,
    agent_workspace_key,
    chat_upload_key,
    export_key,
    get_minio_client,
    rag_source_key,
)

__all__ = [
    "MinioClient",
    "StorageError",
    "agent_workspace_key",
    "chat_upload_key",
    "export_key",
    "get_minio_client",
    "rag_source_key",
]
