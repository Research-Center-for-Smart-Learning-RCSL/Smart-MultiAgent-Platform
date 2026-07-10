"""Regression: create_group must not mismap every IntegrityError to
AgentGroupNameConflict.

Only the uq_agent_groups_project_name_active partial-unique violation is a
genuine name conflict; any other constraint (e.g. a FK violation on
project_id) must surface as its real cause, not a misleading 409 "name
already exists".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from contexts.agent_groups.domain.errors import AgentGroupNameConflict
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository


class _RaisingDb:
    def __init__(self, message: str) -> None:
        self._message = message

    async def execute(self, *_a: Any, **_k: Any) -> None:
        raise IntegrityError("INSERT ...", {}, Exception(self._message))


@pytest.mark.asyncio
async def test_name_conflict_constraint_maps_to_domain_error() -> None:
    db = _RaisingDb(
        'duplicate key value violates unique constraint "uq_agent_groups_project_name_active"'
    )
    repo = AgentGroupRepository(db)  # type: ignore[arg-type]

    with pytest.raises(AgentGroupNameConflict):
        await repo.create_group(project_id=uuid.uuid4(), name="dup")


@pytest.mark.asyncio
async def test_other_integrity_error_is_not_mismapped() -> None:
    db = _RaisingDb(
        'insert or update on table "agent_groups" violates foreign key constraint '
        '"agent_groups_project_id_fkey"'
    )
    repo = AgentGroupRepository(db)  # type: ignore[arg-type]

    with pytest.raises(IntegrityError) as exc:
        await repo.create_group(project_id=uuid.uuid4(), name="whatever")
    assert not isinstance(exc.value, AgentGroupNameConflict)
