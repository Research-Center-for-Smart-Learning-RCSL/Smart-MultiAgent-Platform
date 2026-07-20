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
from enum import StrEnum

_log = logging.getLogger(__name__)


def knowmap_build_job_id(config_id: uuid.UUID, *, target_revision: int) -> str:
    """Stable arq job id keyed on the target corpus revision (F-12).

    A corpus revision advances exactly once per committed document mutation and
    never collides within a second, so two builds for genuinely different corpus
    states get distinct job ids (arq no longer drops the newer one as a duplicate
    of the older), while two enqueues for the *same* revision — the legitimate
    dedup case — still collapse to one queued ``knowmap_build``. This replaces the
    pre-F-12 ``(state, last_build_at@1s)`` nonce, which did not advance on the
    committed corpus change it was meant to represent, so a newer corpus state
    could reuse an older state's retained id and be silently suppressed.
    """
    return f"knowmap:build:{config_id}:{target_revision}"


class EnqueueOutcome(StrEnum):
    """What an enqueue attempt actually achieved (F-4).

    The three cases are indistinguishable to a caller that only sees ``None``,
    which made the revision sweep report every tick as a success even with Redis
    down. Returned so a caller that reports counts can report true ones.
    """

    QUEUED = "queued"
    DEDUPED = "deduped"
    FAILED = "failed"


async def enqueue_knowmap_build(*, config_id: uuid.UUID, target_revision: int) -> EnqueueOutcome:
    """Enqueue a ``knowmap_build`` for ``config_id`` targeting ``target_revision``.

    ``target_revision`` is the config's current ``corpus_revision`` at enqueue
    time — passed both as the job-id discriminator and as a build argument so the
    worker can re-check whether the corpus advanced during the build.

    Best-effort: a failed enqueue is logged, not raised — a missed build is
    recoverable via the explicit rebuild endpoint, the next document change, or
    the revision sweep, and must never fail the triggering upload/delete request.
    A ``None`` return from arq (an already-queued job for the same revision) is a
    *legitimate* same-revision dedup, logged at debug so it is observable and not
    confused with the pre-F-12 silent-suppression bug.

    Trigger callers ignore the return; the sweep uses it to count honestly.
    """
    try:
        from shared_kernel.queue import enqueue

        job = await enqueue(
            "knowmap_build",
            config_id=str(config_id),
            target_revision=target_revision,
            _job_id=knowmap_build_job_id(config_id, target_revision=target_revision),
        )
    except Exception:
        _log.warning("knowmap build enqueue failed for config %s", config_id, exc_info=True)
        return EnqueueOutcome.FAILED
    if job is None:
        _log.debug(
            "knowmap build enqueue deduplicated for config %s revision %d (already queued)",
            config_id,
            target_revision,
        )
        return EnqueueOutcome.DEDUPED
    return EnqueueOutcome.QUEUED


__all__ = ["EnqueueOutcome", "enqueue_knowmap_build", "knowmap_build_job_id"]
