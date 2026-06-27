"""Workspace file use-cases — designer uploads for Code Interpreter.

Files are content-addressed in MinIO (`agent-workspace/{agent_id}/{sha256}`)
and tracked in ``agent_workspace_files`` with a logical workspace path.
Dedup is per-agent: deleting a row whose sha256 is still referenced by
another path for the same agent does NOT delete the MinIO object.
"""

from __future__ import annotations

import hashlib
import posixpath
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import (
    AgentNotFound,
    WorkspaceQuotaExceeded,
)
from contexts.agents.domain.models import WorkspaceFile
from contexts.agents.infrastructure.repositories import (
    AgentRepository,
    WorkspaceFileRepository,
)
from shared_kernel import audit
from shared_kernel.storage.minio_client import MinioClient, agent_workspace_key

_QUOTA_BYTES = 256 * 1024 * 1024  # 256 MB per agent
_MAX_FILE_BYTES = 32 * 1024 * 1024  # 32 MB per multipart upload
_MAX_PATH_LEN = 500


def _safe_workspace_path(raw: str | None, fallback_filename: str) -> str:
    """Validate and normalise a workspace-relative path.

    If *raw* is None, derives a default under ``data/{filename}``.
    Rejects traversal, absolute paths, null bytes, and excessive length.
    """
    if raw is None or not raw.strip():
        name = _safe_input_name(fallback_filename)
        return f"data/{name}"
    if "\x00" in raw:
        raise ValueError("null byte in path")
    normed = posixpath.normpath(raw.strip().replace("\\", "/").strip("/"))
    if normed in (".", "..") or normed.startswith("../") or "/../" in f"/{normed}/":
        raise ValueError("path must not contain '..' components")
    if len(normed) > _MAX_PATH_LEN:
        raise ValueError(f"path too long (max {_MAX_PATH_LEN} chars)")
    if not normed:
        raise ValueError("path must be non-empty")
    return normed


def _safe_input_name(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c for c in base if c.isprintable() and c not in '"\\:*?<>|').strip()
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


class WorkspaceFileService:
    def __init__(self, db: AsyncSession, storage: MinioClient) -> None:
        self._db = db
        self._storage = storage
        self._agents = AgentRepository(db)
        self._files = WorkspaceFileRepository(db)

    async def _require_agent(self, agent_id: uuid.UUID) -> Any:
        agent = await self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFound(str(agent_id))
        return agent

    async def upload(
        self,
        *,
        agent_id: uuid.UUID,
        filename: str,
        data: bytes,
        mime: str,
        path: str | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> WorkspaceFile:
        await self._require_agent(agent_id)

        if len(data) > _MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {_MAX_FILE_BYTES // (1024 * 1024)} MB limit")

        ws_path = _safe_workspace_path(path, filename)
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)

        current_usage = await self._files.total_bytes(agent_id)
        existing = await self._files.get_by_path(agent_id=agent_id, path=ws_path)
        replaced_bytes = existing.size_bytes if existing else 0
        net_usage = current_usage - replaced_bytes + size_bytes
        if net_usage > _QUOTA_BYTES:
            raise WorkspaceQuotaExceeded(
                f"agent workspace quota exceeded ({net_usage} / {_QUOTA_BYTES} bytes)"
            )

        bucket = self._storage._cfg.bucket_agent_workspace
        minio_key = agent_workspace_key(agent_id=agent_id, sha256=sha256)
        await self._storage.put_object(
            bucket=bucket,
            key=minio_key,
            data=data,
            content_type=mime,
        )

        old_sha256 = existing.sha256 if existing else None

        wf = await self._files.upsert(
            agent_id=agent_id,
            path=ws_path,
            size_bytes=size_bytes,
            sha256=sha256,
            mime=mime,
            minio_key=minio_key,
            created_by=actor_user_id,
        )

        if old_sha256 and old_sha256 != sha256:
            ref_count = await self._files.sha256_ref_count(
                agent_id=agent_id, sha256=old_sha256,
            )
            if ref_count == 0:
                old_key = agent_workspace_key(agent_id=agent_id, sha256=old_sha256)
                await self._storage.remove(bucket=bucket, key=old_key)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.workspace_file_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_workspace_file",
                resource_id=wf.id,
                metadata={
                    "agent_id": str(agent_id),
                    "path": ws_path,
                    "size_bytes": size_bytes,
                },
                request_id=request_id,
            ),
        )
        return wf

    async def list_files(self, agent_id: uuid.UUID) -> list[WorkspaceFile]:
        await self._require_agent(agent_id)
        return list(await self._files.list(agent_id))

    async def delete(
        self,
        *,
        agent_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._require_agent(agent_id)
        removed = await self._files.remove(agent_id=agent_id, file_id=file_id)

        ref_count = await self._files.sha256_ref_count(
            agent_id=agent_id,
            sha256=removed.sha256,
        )
        if ref_count == 0:
            bucket = self._storage._cfg.bucket_agent_workspace
            await self._storage.remove(bucket=bucket, key=removed.minio_key)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.workspace_file_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_workspace_file",
                resource_id=file_id,
                metadata={
                    "agent_id": str(agent_id),
                    "path": removed.path,
                },
                request_id=request_id,
            ),
        )


__all__ = ["WorkspaceFileService"]
