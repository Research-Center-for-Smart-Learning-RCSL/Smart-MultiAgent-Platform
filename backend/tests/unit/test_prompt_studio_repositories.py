"""Unit tests for AssistantConfigRepository's IntegrityError mapping (§29 / R29.16).

DB-free: `AsyncSession.execute` is mocked to raise a synthetic IntegrityError
carrying the real Postgres constraint-violation message, matching the
project's established pattern for this kind of test (see
test_agent_service.py's admin_restore_name_reuse_raises_restore_conflict).
Real-DB coverage of the constraint itself lives in
tests/integration/test_migration_0087_schema.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from contexts.prompt_studio.domain.errors import PresetAlreadyActive
from contexts.prompt_studio.domain.models import PromptScope
from contexts.prompt_studio.infrastructure.repositories import AssistantConfigRepository


def _active_index_violation() -> IntegrityError:
    return IntegrityError(
        "INSERT ...",
        {},
        Exception(
            'duplicate key value violates unique constraint "uq_prompt_assistant_config_platform_active"'
        ),
    )


@pytest.mark.asyncio
async def test_create_maps_the_active_index_violation_to_a_domain_error() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=_active_index_violation())
    repo = AssistantConfigRepository(db)

    with pytest.raises(PresetAlreadyActive):
        await repo.create(
            scope=PromptScope.PLATFORM,
            org_id=None,
            user_id=None,
            values={"name": "B", "enabled": True},
        )


@pytest.mark.asyncio
async def test_update_maps_the_active_index_violation_to_a_domain_error() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=_active_index_violation())
    repo = AssistantConfigRepository(db)

    with pytest.raises(PresetAlreadyActive):
        await repo.update(uuid.uuid4(), expected_version=1, values={"enabled": True})


@pytest.mark.asyncio
async def test_update_reraises_an_unrelated_integrity_error() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=IntegrityError("UPDATE ...", {}, Exception("some unrelated constraint"))
    )
    repo = AssistantConfigRepository(db)

    with pytest.raises(IntegrityError):
        await repo.update(uuid.uuid4(), expected_version=1, values={"enabled": True})
