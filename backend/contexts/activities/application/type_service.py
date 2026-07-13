"""Activity-type registration/list/soft-delete (Chapter §30, R30.02).

Validates the payload schema is well-formed JSON Schema and that a referenced
in-process ``validator_id`` is registered, both at registration time. Caller owns
commit; audit rows are emitted on the caller's transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.validators.registry import is_registered
from contexts.activities.application.validators.schema import validate_schema_wellformed
from contexts.activities.domain.errors import (
    ActivityTypeNotFound,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import ActivityType, ValidatorKind
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from shared_kernel import audit


class ActivityTypeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ActivityTypeRepository(db)

    async def register(
        self,
        *,
        project_id: uuid.UUID,
        key: str,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        validate_schema_wellformed(payload_schema)
        self._validate_validator_config(validator_kind, validator_config)
        type_id = await self._repo.create(
            project_id=project_id,
            key=key,
            name=name,
            payload_schema=payload_schema,
            validator_kind=validator_kind,
            validator_config=validator_config,
            retention_days=retention_days,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=type_id,
                metadata={"project_id": str(project_id), "key": key, "validator_kind": validator_kind.value},
                request_id=request_id,
            ),
        )
        created = await self._repo.get(type_id)
        if created is None:  # pragma: no cover — just inserted in this transaction
            raise ActivityTypeNotFound(str(type_id))
        return created

    async def list_types(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        return await self._repo.list_for_project(project_id)

    async def get_type(self, type_id: uuid.UUID) -> ActivityType | None:
        return await self._repo.get(type_id)

    async def soft_delete(
        self,
        *,
        type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        existing = await self._repo.get(type_id)
        if existing is None:
            raise ActivityTypeNotFound(str(type_id))
        await self._repo.soft_delete(type_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=type_id,
                metadata={"project_id": str(existing.project_id)},
                request_id=request_id,
            ),
        )

    @staticmethod
    def _validate_validator_config(kind: ValidatorKind, config: dict[str, Any]) -> None:
        if kind is ValidatorKind.IN_PROCESS:
            vid = str(config.get("validator_id", ""))
            if not vid or not is_registered(vid):
                raise ValidatorConfigInvalid(f"unknown in-process validator_id {vid!r}")
        elif kind is ValidatorKind.WEBHOOK:
            if not str(config.get("url", "")):
                raise ValidatorConfigInvalid("webhook validator requires a 'url'")
        elif kind is ValidatorKind.MCP:
            if not str(config.get("tool_name", "")):
                raise ValidatorConfigInvalid("mcp validator requires a 'tool_name'")
            # agent_id/binding_id are UUID references dispatched by the worker; a
            # non-UUID value here would crash the async worker (uuid.UUID(...)
            # ValueError) into a redelivery loop instead of a clean error verdict,
            # so reject the malformed config at registration.
            for field in ("agent_id", "binding_id"):
                raw = config.get(field)
                if not raw:
                    raise ValidatorConfigInvalid(f"mcp validator requires '{field}'")
                try:
                    uuid.UUID(str(raw))
                except ValueError:
                    raise ValidatorConfigInvalid(f"mcp validator '{field}' must be a UUID") from None


__all__ = ["ActivityTypeService"]
