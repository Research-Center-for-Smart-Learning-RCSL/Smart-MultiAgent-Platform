"""Synchronous in-process validator (activities-platform-core §5.3)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.validators.registry import run_in_process_scorer
from contexts.activities.domain.models import ActivityType, ValidationResult


class InProcessValidator:
    """Runs the registered scoring function for a type's ``validator_id``.

    The payload is assumed schema-valid already (the submission service validates
    it against the type schema before dispatch). The scorer receives the live DB
    session so a first-party function can consult project-owned data tables."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def validate(self, *, activity_type: ActivityType, payload: dict[str, Any]) -> ValidationResult:
        validator_id = str(activity_type.validator_config.get("validator_id", ""))
        return await run_in_process_scorer(validator_id, payload, activity_type, db=self._db)


__all__ = ["InProcessValidator"]
