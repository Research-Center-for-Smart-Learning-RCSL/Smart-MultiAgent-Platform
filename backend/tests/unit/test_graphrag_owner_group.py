"""Unit tests for the singleton owner-group create path (Phase 1).

Guards the regression where a concurrent first-time create for the same agent
raced on ``uq_agent_groups_project_name_active``: the group-insert
``IntegrityError`` must surface as the domain 409 (``GraphRagConfigAlreadyExists``),
the same contract the pre-decouple ``graphrag_configs.agent_id`` UNIQUE gave,
not an unhandled 500.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from contexts.knowledge.application.graphrag_config_service import (
    GraphRagConfigService,
)
from contexts.knowledge.domain.errors import GraphRagConfigAlreadyExists


class _RaceDb:
    """AsyncSession double: SELECT finds no group, the group INSERT conflicts."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _stmt: Any, *a: Any, **kw: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            # The existence probe: no live group yet (both racers see this).
            class _Empty:
                def first(_self) -> Any:  # noqa: N805
                    return None

            return _Empty()
        # The group INSERT: the racing transaction already took the name.
        raise IntegrityError(
            "INSERT INTO agent_groups ...",
            {},
            Exception("duplicate key value violates unique constraint"),
        )


@pytest.mark.asyncio
async def test_ensure_singleton_group_maps_race_conflict_to_409() -> None:
    service = GraphRagConfigService(_RaceDb())  # type: ignore[arg-type]

    with pytest.raises(GraphRagConfigAlreadyExists):
        await service._ensure_singleton_agent_group(
            project_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
        )
