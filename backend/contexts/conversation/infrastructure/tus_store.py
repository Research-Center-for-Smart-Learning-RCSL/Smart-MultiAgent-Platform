"""Redis-backed state for in-flight TUS uploads (§22.15).

Why Redis + local file staging:

  - TUS is inherently *stateful* across HTTP requests. Each PATCH appends to
    the server's prior offset and must be acknowledged with the new offset.
    Storing that handshake state in Redis lets any backend replica serve a
    subsequent PATCH without sticky sessions… as long as the backing bytes
    are on shared storage. In single-node compose we keep bytes on local
    disk; in multi-node prod you swap the `staging_dir` for a shared
    volume (operations runbook §2.3).

  - Abandoned uploads TTL naturally via `EXPIRE` — no sweep worker needed
    for the state. The on-disk `.part` file gets collected by a nightly
    job (see retention.py).

The state is *not* in Postgres — once a TUS upload completes we create one
`message_attachments` row transactionally, and the Redis state is then
deleted. TUS state never needs to survive past completion.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from typing import Final

from shared_kernel.auth.clients import get_redis

_KEY_PREFIX: Final = "tus:upload:"
# 24h per R22.15 abandoned-upload policy.
_TTL_SECONDS: Final = 24 * 3600


@dataclass(frozen=True, slots=True)
class TusUpload:
    """Server-side view of a TUS upload in progress.

    `staging_path` is the absolute path to the `.part` file on the pod's
    local disk. `metadata` is the parsed `Upload-Metadata` header (keys
    already validated by the service layer).
    """

    upload_id: uuid.UUID
    user_id: uuid.UUID
    upload_length: int
    upload_offset: int
    purpose: str  # "chat_attachment" | "rag_source"
    project_id: uuid.UUID
    chatroom_id: uuid.UUID | None
    rag_config_id: uuid.UUID | None
    filename: str
    mime: str
    staging_path: str
    metadata_raw: str  # original header for HEAD echo


def parse_metadata(raw: str) -> dict[str, str]:
    """Parse a TUS `Upload-Metadata` header into {key: decoded-value}.

    Format per RFC: `key1 base64value1,key2 base64value2`. Values without a
    space are allowed (flag-like); we store them as empty strings. Invalid
    base64 raises ValueError so the caller can translate to 400.
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        pair = part.strip().split(" ", 1)
        key = pair[0].strip()
        if not key:
            continue
        if len(pair) == 1:
            out[key] = ""
            continue
        try:
            decoded = base64.b64decode(pair[1].strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid base64 in Upload-Metadata key {key!r}") from exc
        out[key] = decoded
    return out


def _key(upload_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}{upload_id}"


class TusUploadStore:
    """CRUD around the TUS state record. No byte handling — that is the
    service layer's job (local-disk append / MinIO upload)."""

    async def create(self, upload: TusUpload) -> None:
        payload = {
            "upload_id": str(upload.upload_id),
            "user_id": str(upload.user_id),
            "upload_length": upload.upload_length,
            "upload_offset": upload.upload_offset,
            "purpose": upload.purpose,
            "project_id": str(upload.project_id),
            "chatroom_id": str(upload.chatroom_id) if upload.chatroom_id else None,
            "rag_config_id": str(upload.rag_config_id) if upload.rag_config_id else None,
            "filename": upload.filename,
            "mime": upload.mime,
            "staging_path": upload.staging_path,
            "metadata_raw": upload.metadata_raw,
        }
        await get_redis().set(
            _key(upload.upload_id),
            json.dumps(payload),
            ex=_TTL_SECONDS,
        )

    async def get(self, upload_id: uuid.UUID) -> TusUpload | None:
        raw = await get_redis().get(_key(upload_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return TusUpload(
            upload_id=uuid.UUID(data["upload_id"]),
            user_id=uuid.UUID(data["user_id"]),
            upload_length=int(data["upload_length"]),
            upload_offset=int(data["upload_offset"]),
            purpose=data["purpose"],
            project_id=uuid.UUID(data["project_id"]),
            chatroom_id=(uuid.UUID(data["chatroom_id"]) if data.get("chatroom_id") else None),
            rag_config_id=(uuid.UUID(data["rag_config_id"]) if data.get("rag_config_id") else None),
            filename=data["filename"],
            mime=data["mime"],
            staging_path=data["staging_path"],
            metadata_raw=data["metadata_raw"],
        )

    async def update_offset(
        self,
        upload_id: uuid.UUID,
        new_offset: int,
    ) -> None:
        raw = await get_redis().get(_key(upload_id))
        if raw is None:
            return
        data = json.loads(raw)
        data["upload_offset"] = new_offset
        await get_redis().set(_key(upload_id), json.dumps(data), ex=_TTL_SECONDS)

    async def delete(self, upload_id: uuid.UUID) -> None:
        await get_redis().delete(_key(upload_id))


__all__ = ["TusUpload", "TusUploadStore", "parse_metadata"]
