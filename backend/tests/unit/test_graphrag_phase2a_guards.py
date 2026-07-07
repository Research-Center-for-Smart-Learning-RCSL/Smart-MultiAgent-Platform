"""Regression guards for Phase 2a D4 and D9.

Both defects were already resolved by the Phase 1 rewrite:

- D4 (AC-7): ``list_for_agents`` de-duplicates configs, so a multi-member owner
  group with two matched members returns the config once, not twice.
- D9 (AC-12): the reconciler constructs no ``GraphRagBuilder`` inline — the only
  construction seam is the ``graphrag_build`` task.

These tests lock those properties in so a future change cannot silently
reintroduce the double-increment (D4) or a second builder-construction site (D9).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository

# ---------------------------------------------------------------------------
# D4 (AC-7) — the trigger resolver returns each config at most once
# ---------------------------------------------------------------------------


def _config_row(config_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=config_id,
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state="idle",
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
        embed_provider=None,
        embed_model=None,
        embed_dim=None,
    )


class _DupResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _DupDb:
    """AsyncSession double whose membership-join query returns the config twice
    (as it would for a two-member group with both members matched)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, *_a: Any, **_k: Any) -> _DupResult:
        return _DupResult(self._rows)


@pytest.mark.asyncio
async def test_list_for_agents_dedupes_multi_member_group() -> None:
    config_id = uuid.uuid4()
    # The join yields one row per matched member — here two rows for one config.
    db = _DupDb([_config_row(config_id), _config_row(config_id)])
    repo = GraphRagConfigRepository(db)  # type: ignore[arg-type]

    out = await repo.list_for_agents([uuid.uuid4(), uuid.uuid4()])

    # DISTINCT (AC-7): the config appears exactly once regardless of member count,
    # so its message counter is incremented and its build enqueued only once.
    assert [c.id for c in out] == [config_id]


# ---------------------------------------------------------------------------
# D9 (AC-12) — the reconciler constructs no builder inline
# ---------------------------------------------------------------------------


def test_reconciler_has_no_inline_builder_construction() -> None:
    import app.workers.graphrag_reconciler as worker_recon
    import contexts.knowledge.application.graphrag_reconciler as app_recon

    for module in (worker_recon, app_recon):
        src = inspect.getsource(module)
        assert "GraphRagBuilder(" not in src, (
            f"{module.__name__} constructs a GraphRagBuilder inline; the only "
            "construction seam must be the graphrag_build task (D9)."
        )
