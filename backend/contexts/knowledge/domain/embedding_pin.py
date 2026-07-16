"""Durable project embedding-pin domain types (F-11).

A per-``(project, kind)`` embedding pin that outlives any single config, so a
project's fixed-size Qdrant collection dimension survives config deletion and
cannot be changed by a concurrent first-create race. The three kinds map to the
three per-project collections: ``rag_{project_id}`` (file RAG),
``knowmap_{project_id}`` (Knowledge Map), and ``graphrag_{project_id}``
(Concept Map).
"""

from __future__ import annotations

import enum


class PinKind(str, enum.Enum):
    FILE_RAG = "file_rag"
    KNOWMAP = "knowmap"
    GRAPHRAG = "graphrag"


class TeardownOutcome(str, enum.Enum):
    """Result of a configless collection teardown (F-3).

    The pin may be released only on ``DROPPED`` or ``ABSENT`` — the two outcomes
    where Qdrant has confirmed the project's collection no longer exists. ``FAILED``
    retains the pin so the invariant fails closed: a later different-dimension config
    stays rejected until a retry confirms absence. ``SKIPPED_LIVE_CONFIG`` means a
    config was created (or survived) under the lock, so there is no orphan to drop
    and the pin legitimately stays.
    """

    DROPPED = "dropped"
    ABSENT = "absent"
    SKIPPED_LIVE_CONFIG = "skipped_live_config"
    FAILED = "failed"

    @property
    def pin_released(self) -> bool:
        return self in (TeardownOutcome.DROPPED, TeardownOutcome.ABSENT)


__all__ = ["PinKind", "TeardownOutcome"]
