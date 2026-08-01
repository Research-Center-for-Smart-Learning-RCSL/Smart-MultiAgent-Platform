"""Application-owned contracts for resumable-upload state and finalization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


class TusReserveResult(IntEnum):
    ACCEPTED = 1
    USER_ACTIVE_LIMIT = -1
    USER_BYTE_LIMIT = -2
    PROJECT_ACTIVE_LIMIT = -3
    PROJECT_BYTE_LIMIT = -4
    HOST_ACTIVE_LIMIT = -5
    HOST_BYTE_LIMIT = -6


class TusOffsetUpdateResult(IntEnum):
    ACCEPTED = 1
    MISMATCH = 0
    MISSING = -1
    USER_HOURLY_LIMIT = -2
    PROJECT_HOURLY_LIMIT = -3


@dataclass(frozen=True, slots=True)
class TusUpload:
    upload_id: uuid.UUID
    user_id: uuid.UUID
    upload_length: int
    upload_offset: int
    purpose: str
    project_id: uuid.UUID
    chatroom_id: uuid.UUID | None
    rag_config_id: uuid.UUID | None
    knowmap_config_id: uuid.UUID | None
    filename: str
    mime: str
    staging_path: str
    metadata_raw: str


class TusUploadStorePort(Protocol):
    async def create(
        self,
        upload: TusUpload,
        *,
        host_max_reserved_bytes: int,
    ) -> TusReserveResult: ...

    async def get(self, upload_id: uuid.UUID) -> TusUpload | None: ...

    async def update_offset(
        self,
        upload_id: uuid.UUID,
        expected_offset: int,
        new_offset: int,
        *,
        quota_hour: int | None = None,
    ) -> TusOffsetUpdateResult: ...

    async def rollback_offset(
        self,
        upload: TusUpload,
        expected_offset: int,
        new_offset: int,
        *,
        quota_hour: int | None = None,
    ) -> bool: ...

    async def delete(self, upload_id: uuid.UUID) -> None: ...


class FinalizedKnowledgeDocument(Protocol):
    @property
    def id(self) -> uuid.UUID: ...


class KnowledgeUploadFinalizer(Protocol):
    async def finalize_knowmap_upload(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> FinalizedKnowledgeDocument: ...

    async def finalize_rag_upload(
        self,
        *,
        rag_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> FinalizedKnowledgeDocument: ...


__all__ = [
    "KnowledgeUploadFinalizer",
    "TusOffsetUpdateResult",
    "TusReserveResult",
    "TusUpload",
    "TusUploadStorePort",
]
