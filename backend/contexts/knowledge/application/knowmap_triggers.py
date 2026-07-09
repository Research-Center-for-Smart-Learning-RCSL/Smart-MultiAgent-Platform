"""Knowledge Map build-trigger enqueue (Phase 3, Q-3 / AC-6).

A Knowledge Map rebuilds on document-set change (upload / delete / reprocess) and
on explicit designer rebuild — never on a conversation message (that is the Concept
Map's trigger). This module owns the stable ``_job_id`` (Phase 2a dedup analogue) so
a burst of document changes between two builds collapses to a single queued
``knowmap_build``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from contexts.knowledge.domain.graphrag import BuildState

_log = logging.getLogger(__name__)


def knowmap_build_job_id(
    config_id: uuid.UUID,
    *,
    last_build_state: BuildState,
    last_build_at: datetime | None,
) -> str:
    """Stable per-rebuild-cycle arq job id for build de-duplication.

    Every trigger fired between two builds resolves to the same id, so concurrent
    document changes collapse to a single queued ``knowmap_build`` (arq returns
    ``None`` for a duplicate id). The nonce — the config's terminal state plus its
    ``last_build_at`` epoch — advances once a build completes or the state flips,
    so a legitimate rebuild is never suppressed by the prior job's retained result.
    ``0`` stands in for a config that has never built.
    """
    epoch = int(last_build_at.timestamp()) if last_build_at is not None else 0
    return f"knowmap:build:{config_id}:{last_build_state.value}:{epoch}"


async def enqueue_knowmap_build(
    *,
    config_id: uuid.UUID,
    last_build_state: BuildState,
    last_build_at: datetime | None,
) -> None:
    """Enqueue a ``knowmap_build`` for ``config_id`` with the dedup job id.

    Best-effort: a failed enqueue is logged, not raised — a missed build is
    recoverable via the explicit rebuild endpoint or the next document change,
    and must never fail the triggering upload/delete request.
    """
    try:
        from shared_kernel.queue import enqueue

        await enqueue(
            "knowmap_build",
            config_id=str(config_id),
            _job_id=knowmap_build_job_id(
                config_id, last_build_state=last_build_state, last_build_at=last_build_at
            ),
        )
    except Exception:
        _log.warning("knowmap build enqueue failed for config %s", config_id, exc_info=True)


__all__ = ["enqueue_knowmap_build", "knowmap_build_job_id"]
