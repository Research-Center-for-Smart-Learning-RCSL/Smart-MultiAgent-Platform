"""Unit tests for Knowledge Map build-trigger dedup (Phase 3, Q-3 / AC-6).

A rebuild is triggered by a document-set change (upload / delete / reprocess) or an
explicit designer rebuild — never by a conversation message. Every trigger fired
between two builds must resolve to the *same* arq job id so a burst collapses to one
queued build; a completed build (state or ``last_build_at`` advance) must mint a fresh
id so the next legitimate rebuild is not suppressed by the prior job's retained result.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from contexts.knowledge.application.knowmap_triggers import (
    enqueue_knowmap_build,
    knowmap_build_job_id,
)
from contexts.knowledge.domain.graphrag import BuildState

_CONFIG = uuid.uuid4()
_AT = datetime(2026, 7, 7, 12, 0, 0)


class TestJobId:
    def test_stable_within_a_rebuild_cycle(self) -> None:
        a = knowmap_build_job_id(_CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT)
        b = knowmap_build_job_id(_CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT)
        assert a == b

    def test_advances_when_last_build_at_moves(self) -> None:
        before = knowmap_build_job_id(
            _CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT
        )
        after = knowmap_build_job_id(
            _CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT + timedelta(seconds=1)
        )
        assert before != after

    def test_advances_when_state_flips(self) -> None:
        built = knowmap_build_job_id(_CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT)
        failed = knowmap_build_job_id(_CONFIG, last_build_state=BuildState.FAILED, last_build_at=_AT)
        assert built != failed

    def test_never_built_uses_zero_epoch(self) -> None:
        jid = knowmap_build_job_id(_CONFIG, last_build_state=BuildState.IDLE, last_build_at=None)
        assert jid == f"knowmap:build:{_CONFIG}:{BuildState.IDLE.value}:0"

    def test_distinct_configs_never_collide(self) -> None:
        other = uuid.uuid4()
        assert knowmap_build_job_id(
            _CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT
        ) != knowmap_build_job_id(other, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT)


class TestEnqueue:
    async def test_passes_dedup_job_id_to_queue(self) -> None:
        enqueue_mock = AsyncMock()
        with patch("shared_kernel.queue.enqueue", enqueue_mock):
            await enqueue_knowmap_build(
                config_id=_CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT
            )
        enqueue_mock.assert_awaited_once()
        _, kwargs = enqueue_mock.call_args
        assert kwargs["config_id"] == str(_CONFIG)
        assert kwargs["_job_id"] == knowmap_build_job_id(
            _CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT
        )

    async def test_enqueue_failure_is_swallowed(self) -> None:
        # Best-effort: a failed enqueue must never fail the triggering request.
        with patch("shared_kernel.queue.enqueue", AsyncMock(side_effect=RuntimeError("down"))):
            await enqueue_knowmap_build(
                config_id=_CONFIG, last_build_state=BuildState.QDRANT_COMMITTED, last_build_at=_AT
            )  # no raise
