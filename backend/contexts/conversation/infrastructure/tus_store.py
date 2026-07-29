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

import json
import time
import uuid
from typing import Final

from contexts.conversation.application.tus_ports import (
    TusOffsetUpdateResult,
    TusReserveResult,
    TusUpload,
)
from shared_kernel.auth.clients import get_redis

_KEY_PREFIX: Final = "tus:upload:"
_RESERVATION_PREFIX: Final = "tus:reservation:"
_QUOTA_PREFIX: Final = "tus:quota:"
# 24h per R22.15 abandoned-upload policy.
_TTL_SECONDS: Final = 24 * 3600
_RESERVATION_KEY_TTL_SECONDS: Final = _TTL_SECONDS + 3600
_QUOTA_KEY_TTL_SECONDS: Final = 2 * 3600

TUS_USER_MAX_ACTIVE: Final = 2
TUS_USER_MAX_RESERVED_BYTES: Final = 2 * 1024 * 1024 * 1024
TUS_PROJECT_MAX_ACTIVE: Final = 4
TUS_PROJECT_MAX_RESERVED_BYTES: Final = 4 * 1024 * 1024 * 1024
TUS_HOST_MAX_ACTIVE: Final = 16
TUS_HOST_MAX_RESERVED_BYTES: Final = 8 * 1024 * 1024 * 1024
TUS_USER_HOURLY_BYTES: Final = 2 * 1024 * 1024 * 1024
TUS_PROJECT_HOURLY_BYTES: Final = 8 * 1024 * 1024 * 1024


def _key(upload_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}{upload_id}"


def _reservation_keys(scope: str, scope_id: uuid.UUID | str) -> tuple[str, str]:
    stem = f"{_RESERVATION_PREFIX}{scope}:{scope_id}"
    return f"{stem}:expires", f"{stem}:bytes"


def _quota_key(scope: str, scope_id: uuid.UUID, hour: int) -> str:
    return f"{_QUOTA_PREFIX}{scope}:{scope_id}:{hour}"


class TusUploadStore:
    """CRUD around the TUS state record. No byte handling — that is the
    service layer's job (local-disk append / MinIO upload)."""

    _CREATE_SCRIPT = """
local function prune(expiry_key, bytes_key, now)
    local expired = redis.call('ZRANGEBYSCORE', expiry_key, '-inf', now)
    if #expired > 0 then
        redis.call('ZREM', expiry_key, unpack(expired))
        redis.call('HDEL', bytes_key, unpack(expired))
    end
end
local function reserved(bytes_key)
    local values = redis.call('HVALS', bytes_key)
    local total = 0
    for _, value in ipairs(values) do total = total + tonumber(value) end
    return total
end

if redis.call('EXISTS', KEYS[1]) == 1 then return 1 end
for index = 2, 6, 2 do prune(KEYS[index], KEYS[index + 1], tonumber(ARGV[2])) end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return -1 end
if reserved(KEYS[3]) + tonumber(ARGV[1]) > tonumber(ARGV[5]) then return -2 end
if redis.call('ZCARD', KEYS[4]) >= tonumber(ARGV[6]) then return -3 end
if reserved(KEYS[5]) + tonumber(ARGV[1]) > tonumber(ARGV[7]) then return -4 end
if redis.call('ZCARD', KEYS[6]) >= tonumber(ARGV[8]) then return -5 end
if reserved(KEYS[7]) + tonumber(ARGV[1]) > tonumber(ARGV[9]) then return -6 end

redis.call('SET', KEYS[1], ARGV[3], 'EX', tonumber(ARGV[10]))
for index = 2, 6, 2 do
    redis.call('ZADD', KEYS[index], tonumber(ARGV[2]) + tonumber(ARGV[10]), ARGV[11])
    redis.call('HSET', KEYS[index + 1], ARGV[11], ARGV[1])
    redis.call('EXPIRE', KEYS[index], tonumber(ARGV[12]))
    redis.call('EXPIRE', KEYS[index + 1], tonumber(ARGV[12]))
end
return 1
"""

    async def create(
        self,
        upload: TusUpload,
        *,
        host_max_reserved_bytes: int = TUS_HOST_MAX_RESERVED_BYTES,
    ) -> TusReserveResult:
        payload = {
            "upload_id": str(upload.upload_id),
            "user_id": str(upload.user_id),
            "upload_length": upload.upload_length,
            "upload_offset": upload.upload_offset,
            "purpose": upload.purpose,
            "project_id": str(upload.project_id),
            "chatroom_id": str(upload.chatroom_id) if upload.chatroom_id else None,
            "rag_config_id": str(upload.rag_config_id) if upload.rag_config_id else None,
            "knowmap_config_id": (str(upload.knowmap_config_id) if upload.knowmap_config_id else None),
            "filename": upload.filename,
            "mime": upload.mime,
            "staging_path": upload.staging_path,
            "metadata_raw": upload.metadata_raw,
        }
        now_epoch = int(time.time())
        user_expiry, user_bytes = _reservation_keys("user", upload.user_id)
        project_expiry, project_bytes = _reservation_keys("project", upload.project_id)
        host_expiry, host_bytes = _reservation_keys("host", "local")
        result = await get_redis().eval(
            self._CREATE_SCRIPT,
            7,
            _key(upload.upload_id),
            user_expiry,
            user_bytes,
            project_expiry,
            project_bytes,
            host_expiry,
            host_bytes,
            str(upload.upload_length),
            str(now_epoch),
            json.dumps(payload),
            str(TUS_USER_MAX_ACTIVE),
            str(TUS_USER_MAX_RESERVED_BYTES),
            str(TUS_PROJECT_MAX_ACTIVE),
            str(TUS_PROJECT_MAX_RESERVED_BYTES),
            str(TUS_HOST_MAX_ACTIVE),
            str(min(TUS_HOST_MAX_RESERVED_BYTES, host_max_reserved_bytes)),
            str(_TTL_SECONDS),
            str(upload.upload_id),
            str(_RESERVATION_KEY_TTL_SECONDS),
        )
        return TusReserveResult(int(result))

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
            knowmap_config_id=(
                uuid.UUID(data["knowmap_config_id"]) if data.get("knowmap_config_id") else None
            ),
            filename=data["filename"],
            mime=data["mime"],
            staging_path=data["staging_path"],
            metadata_raw=data["metadata_raw"],
        )

    # Atomic CAS via Lua so two concurrent PATCHes cannot both read the same
    # offset and silently overwrite each other (H11).
    _CAS_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return -1 end
local data = cjson.decode(raw)
if tostring(data['upload_offset']) ~= ARGV[1] then return 0 end
local user_total = tonumber(redis.call('GET', KEYS[2]) or '0')
local project_total = tonumber(redis.call('GET', KEYS[3]) or '0')
local chunk_bytes = tonumber(ARGV[2]) - tonumber(ARGV[1])
if user_total + chunk_bytes > tonumber(ARGV[4]) then return -2 end
if project_total + chunk_bytes > tonumber(ARGV[5]) then return -3 end
redis.call('INCRBY', KEYS[2], chunk_bytes)
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[6]))
redis.call('INCRBY', KEYS[3], chunk_bytes)
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[6]))
data['upload_offset'] = tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(data), 'EX', tonumber(ARGV[3]))
-- 4/5 user, 6/7 project, 8/9 host: every tier's expiry ZSET and byte hash must be
-- refreshed, or the omitted tier's member is pruned by _CREATE_SCRIPT while the
-- upload is still live and stops counting against its cap.
for index = 4, 8, 2 do
    redis.call('ZADD', KEYS[index], tonumber(ARGV[7]) + tonumber(ARGV[3]), ARGV[8])
    redis.call('EXPIRE', KEYS[index], tonumber(ARGV[9]))
    redis.call('EXPIRE', KEYS[index + 1], tonumber(ARGV[9]))
end
return 1
"""

    async def update_offset(
        self,
        upload_id: uuid.UUID,
        expected_offset: int,
        new_offset: int,
        *,
        quota_hour: int | None = None,
    ) -> TusOffsetUpdateResult:
        """Atomically advance offset only if it still equals *expected_offset*.

        The offset claim and hourly-byte reservation are one Redis operation.
        """
        upload = await self.get(upload_id)
        if upload is None:
            return TusOffsetUpdateResult.MISSING
        hour = quota_hour if quota_hour is not None else int(time.time() // 3600)
        user_expiry, user_bytes = _reservation_keys("user", upload.user_id)
        project_expiry, project_bytes = _reservation_keys("project", upload.project_id)
        host_expiry, host_bytes = _reservation_keys("host", "local")
        result = await get_redis().eval(
            self._CAS_SCRIPT,
            9,
            _key(upload_id),
            _quota_key("user", upload.user_id, hour),
            _quota_key("project", upload.project_id, hour),
            user_expiry,
            user_bytes,
            project_expiry,
            project_bytes,
            host_expiry,
            host_bytes,
            str(expected_offset),
            str(new_offset),
            str(_TTL_SECONDS),
            str(TUS_USER_HOURLY_BYTES),
            str(TUS_PROJECT_HOURLY_BYTES),
            str(_QUOTA_KEY_TTL_SECONDS),
            str(int(time.time())),
            str(upload_id),
            str(_RESERVATION_KEY_TTL_SECONDS),
        )
        return TusOffsetUpdateResult(int(result))

    _ROLLBACK_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return -1 end
local data = cjson.decode(raw)
if tostring(data['upload_offset']) ~= ARGV[1] then return 0 end
local chunk_bytes = tonumber(ARGV[1]) - tonumber(ARGV[2])
for index = 2, 3 do
    local current = tonumber(redis.call('GET', KEYS[index]) or '0')
    redis.call('SET', KEYS[index], math.max(0, current - chunk_bytes), 'EX', tonumber(ARGV[4]))
end
data['upload_offset'] = tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(data), 'EX', tonumber(ARGV[3]))
return 1
"""

    async def rollback_offset(
        self,
        upload: TusUpload,
        expected_offset: int,
        new_offset: int,
        *,
        quota_hour: int | None = None,
    ) -> bool:
        hour = quota_hour if quota_hour is not None else int(time.time() // 3600)
        result = await get_redis().eval(
            self._ROLLBACK_SCRIPT,
            3,
            _key(upload.upload_id),
            _quota_key("user", upload.user_id, hour),
            _quota_key("project", upload.project_id, hour),
            str(expected_offset),
            str(new_offset),
            str(_TTL_SECONDS),
            str(_QUOTA_KEY_TTL_SECONDS),
        )
        return int(result) == 1

    _DELETE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
redis.call('DEL', KEYS[1])
for index = 2, 6, 2 do
    redis.call('ZREM', KEYS[index], ARGV[1])
    redis.call('HDEL', KEYS[index + 1], ARGV[1])
end
return 1
"""

    async def delete(self, upload_id: uuid.UUID) -> None:
        upload = await self.get(upload_id)
        if upload is None:
            return
        user_expiry, user_bytes = _reservation_keys("user", upload.user_id)
        project_expiry, project_bytes = _reservation_keys("project", upload.project_id)
        host_expiry, host_bytes = _reservation_keys("host", "local")
        await get_redis().eval(
            self._DELETE_SCRIPT,
            7,
            _key(upload_id),
            user_expiry,
            user_bytes,
            project_expiry,
            project_bytes,
            host_expiry,
            host_bytes,
            str(upload_id),
        )


__all__ = [
    "TusUploadStore",
]
