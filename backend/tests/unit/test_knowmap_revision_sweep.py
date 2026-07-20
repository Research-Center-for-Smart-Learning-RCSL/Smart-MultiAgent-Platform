"""F-4 — the Knowledge Map revision-divergence sweep.

Finalization is edge-triggered and best-effort (``knowmap_build`` swallows a failed
``_finalize_build_revision``, and ``enqueue_knowmap_build`` swallows queue errors), so a
committed ``corpus_revision`` can have no queued build. This sweep is the level-triggered
backstop: it reads the durable gap and re-offers the work.

The AC-1 finalizer-failure regression lives in ``test_knowmap_build_dedup.py`` beside the
rest of the revision machinery; this file covers the query predicates and the sweep's
orchestration (paging, per-tick cap, failure isolation).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).replace(" ", "").lower()


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result([])


# ---------------------------------------------------------------------------
# Repository predicates (AC-3, AC-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_divergence_query_selects_only_live_idle_divergent_configs() -> None:
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_revision_divergent(limit=50, offset=100)

    sql = _sql(db.statements[0])
    # Deleted configs are excluded (AC-4): their graph is being torn down anyway.
    assert "deleted_atisnull" in sql
    # IDLE only (Q-2): in-flight has a completion path, FAILED is a deliberate stop.
    assert "last_build_state='idle'" in sql
    # The divergence itself. COALESCE covers a config that has never built, and it
    # is also what makes revision zero unselectable without an explicit clause.
    assert "corpus_revision>coalesce(knowmap_configs.built_corpus_revision,0)" in sql
    # Stable ordering, or successive pages of one tick would overlap or skip rows.
    assert "orderbyknowmap_configs.id" in sql
    assert "limit50" in sql
    assert "offset100" in sql


@pytest.mark.asyncio
async def test_divergence_query_does_not_filter_on_other_build_states() -> None:
    # Guard against a future edit widening the state predicate: RUNNING and FAILED
    # must never appear, or the sweep would fight the reconciler / retry a stop.
    db = _CaptureSession()
    repo = KnowmapConfigRepository(db)  # type: ignore[arg-type]
    await repo.list_revision_divergent(limit=1, offset=0)

    sql = _sql(db.statements[0])
    assert "'running'" not in sql
    assert "'failed'" not in sql
