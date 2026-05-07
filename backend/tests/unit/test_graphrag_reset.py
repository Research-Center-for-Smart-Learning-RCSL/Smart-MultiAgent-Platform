"""Unit tests for the admin reset path (R11a.02).

Verifies :meth:`GraphRagConfigService.admin_reset` force-sets the config
to ``idle`` and emits an ``admin.graphrag_reset`` audit row carrying the
previous state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_config_service import (
    GraphRagConfigService,
)
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


class RecordingDb:
    """Minimal AsyncSession double — records every ``execute`` call."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
        self.calls.append(stmt)

        class _R:
            def one(_self) -> Any:  # noqa: N805
                return None

            def first(_self) -> Any:  # noqa: N805
                return None

            def all(_self) -> list[Any]:  # noqa: N805
                return []

        return _R()


class FakeRepo:
    def __init__(self, cfg: GraphRagConfig) -> None:
        self._cfg = cfg
        self.sets: list[dict[str, Any]] = []

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False):
        return self._cfg

    async def set_state(self, **kw: Any) -> None:
        self.sets.append(kw)
        # Mutate in place so subsequent gets see the new state.
        self._cfg = GraphRagConfig(
            id=self._cfg.id,
            project_id=self._cfg.project_id,
            agent_id=self._cfg.agent_id,
            builder_key_group_id=self._cfg.builder_key_group_id,
            trigger_config=self._cfg.trigger_config,
            last_build_at=self._cfg.last_build_at,
            last_build_state=kw["state"],
            last_build_error=kw.get("error"),
            created_at=self._cfg.created_at,
            deleted_at=self._cfg.deleted_at,
        )


@pytest.mark.asyncio()
async def test_admin_reset_forces_idle_and_audits() -> None:
    cfg = GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=BuildState.FAILED_COMPENSATING,
        last_build_error="qdrant down",
        created_at=datetime.now(UTC),
        deleted_at=None,
    )
    db = RecordingDb()
    service = GraphRagConfigService(db)  # type: ignore[arg-type]
    repo = FakeRepo(cfg)
    service._configs = repo  # type: ignore[assignment, attr-defined]

    out = await service.admin_reset(
        config_id=cfg.id,
        actor_user_id=uuid.uuid4(),
        actor_ip="127.0.0.1",
        request_id=uuid.uuid4(),
    )

    assert out.last_build_state is BuildState.IDLE
    assert out.last_build_error is None
    # set_state was called exactly once with IDLE, no error, no stamp.
    assert len(repo.sets) == 1
    assert repo.sets[0]["state"] is BuildState.IDLE
    assert repo.sets[0]["error"] is None

    # The audit row should have been inserted — one execute call on the DB.
    assert len(db.calls) == 1
    compiled = str(db.calls[0])
    assert "audit_logs" in compiled.lower()
