"""Policy for uploads that resolve to an existing document row."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from enum import StrEnum

from contexts.knowledge.domain.models import DocumentStatus


class ReuploadAction(StrEnum):
    DEDUP_NOOP = "dedup_noop"
    CONFLICT = "conflict"
    REINDEX_WITH_OVERWRITE = "reindex_with_overwrite"


def resolve_existing_document(
    *,
    status: DocumentStatus,
    stored_agent_ids: Sequence[uuid.UUID],
    submitted_agent_ids: Sequence[uuid.UUID],
) -> ReuploadAction:
    if status is not DocumentStatus.READY:
        return ReuploadAction.REINDEX_WITH_OVERWRITE
    if set(stored_agent_ids) == set(submitted_agent_ids):
        return ReuploadAction.DEDUP_NOOP
    return ReuploadAction.CONFLICT


__all__ = ["ReuploadAction", "resolve_existing_document"]
