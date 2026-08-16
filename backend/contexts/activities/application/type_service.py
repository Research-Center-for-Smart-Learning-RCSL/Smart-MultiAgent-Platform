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

from contexts.activities.application.policy_service import ActivityPolicyService
from contexts.activities.application.validators.registry import get_config_validator, is_registered
from contexts.activities.application.validators.schema import validate_schema_wellformed
from contexts.activities.domain.errors import (
    ActivityTypeActive,
    ActivityTypeNotFound,
    PlatformActivityTypeReadOnly,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import ActivityType, ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from shared_kernel import audit


class ActivityTypeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ActivityTypeRepository(db)
        self._activation_repo = ActivationRepository(db)
        self._policy = ActivityPolicyService(db)

    async def register(
        self,
        *,
        project_id: uuid.UUID | None,
        key: str,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        expose_payload_to_agent: bool = True,
        echo_includes_content: bool = False,
        scope: ActivityTypeScope = ActivityTypeScope.PROJECT,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        if (project_id is None) is (scope is ActivityTypeScope.PROJECT):
            # Caught by ck_activity_types_project_scope anyway, but a 500 from a
            # CHECK is a worse diagnosis than naming the inconsistent argument.
            raise ValueError(f"scope {scope.value!r} does not agree with project_id={project_id!r}")
        validate_schema_wellformed(payload_schema)
        self._validate_validator_config(validator_kind, validator_config)
        # [R30.30] first gate: reject a violating type at authoring time so the
        # activation-time gate seldom has to fire in front of a class.
        await self._policy.assert_allows(
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            retention_days=retention_days,
        )
        type_id = await self._repo.create(
            project_id=project_id,
            key=key,
            name=name,
            payload_schema=payload_schema,
            validator_kind=validator_kind,
            validator_config=validator_config,
            retention_days=retention_days,
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            scope=scope,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=type_id,
                metadata={
                    "project_id": str(project_id) if project_id is not None else None,
                    "scope": scope.value,
                    "key": key,
                    "validator_kind": validator_kind.value,
                },
                request_id=request_id,
            ),
        )
        created = await self._repo.get(type_id)
        if created is None:  # pragma: no cover — just inserted in this transaction
            raise ActivityTypeNotFound(str(type_id))
        return created

    async def update(
        self,
        *,
        project_id: uuid.UUID,
        type_id: uuid.UUID,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        expose_payload_to_agent: bool = True,
        echo_includes_content: bool = False,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        """Edit an existing type's fields (``key`` is never editable, R30.23).

        Safe metadata (``name``, ``retention_days``, ``expose_payload_to_agent``,
        ``echo_includes_content``) may change any time. A change to a behavioral
        field (``payload_schema``/``validator_kind``/``validator_config``) re-runs
        registration's validators, bumps ``version``, and is rejected while any
        active activation references the type — otherwise an in-flight activation
        would desync. The ``project_id`` guard keeps this tenant-safe (mirrors
        ``delete_type``).

        A platform-scoped type is read-only here ([R30.23]): 403, not the 404 the
        cross-project guard returns, because the type legitimately appears in this
        owner's list once opted in — what they lack is the capability, and only a
        platform admin has it (:meth:`update_platform_type`). The scope check runs
        before the project comparison because a platform row's ``project_id`` is
        NULL and would otherwise fall through to a misleading "not found".
        """
        existing = await self._repo.get(type_id)
        if existing is None:
            raise ActivityTypeNotFound(str(type_id))
        if existing.scope is ActivityTypeScope.PLATFORM:
            raise PlatformActivityTypeReadOnly(str(type_id))
        if existing.project_id != project_id:
            raise ActivityTypeNotFound(str(type_id))

        behavioral_changed = (
            payload_schema != existing.payload_schema
            or validator_kind != existing.validator_kind
            or validator_config != existing.validator_config
        )
        if behavioral_changed:
            validate_schema_wellformed(payload_schema)
            self._validate_validator_config(validator_kind, validator_config)
            if await self._activation_repo.list_active_for_type(type_id):
                raise ActivityTypeActive(str(type_id))

        # Outside the `behavioral_changed` branch on purpose: the three governance
        # fields are "safe metadata" under [R30.23] and may be edited at any time,
        # so an owner could flip expose_payload_to_agent without touching the
        # schema or validator. Checking only behavioral edits would leave the
        # policy trivially bypassable ([R30.30]).
        await self._policy.assert_allows(
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            retention_days=retention_days,
        )

        await self._repo.update(
            type_id,
            name=name,
            payload_schema=payload_schema,
            validator_kind=validator_kind,
            validator_config=validator_config,
            retention_days=retention_days,
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            bump_version=behavioral_changed,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=type_id,
                metadata={"project_id": str(project_id), "version_bumped": behavioral_changed},
                request_id=request_id,
            ),
        )
        updated = await self._repo.get(type_id)
        if updated is None:  # pragma: no cover — just updated in this transaction
            raise ActivityTypeNotFound(str(type_id))
        return updated

    async def update_platform_type(
        self,
        *,
        type_id: uuid.UUID,
        name: str,
        retention_days: int | None,
        expose_payload_to_agent: bool,
        echo_includes_content: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        """The platform admin's edit of an installed example ([R30.23], Q-4).

        Four fields, and no more: the ones a governance-policy conflict can
        involve. ``key``, ``payload_schema`` and ``validator_config`` are not
        parameters at all rather than being validated away — the moment
        ``payload_schema`` is editable from the admin UI this stops being an
        install surface and becomes a course-authoring CMS, which §10 of the
        dossier rules out explicitly.

        This exists because the alternative is a dead end: both shipped examples
        set ``expose_payload_to_agent: true``, so an admin who locks that flag to
        false blocks them at activation with nobody able to fix it. The fix is that
        the owner of the row can edit the row.

        The policy is re-asserted rather than bypassed — an admin edits an example
        *into* compliance, they do not exempt it ([R30.30]).
        """
        existing = await self._repo.get(type_id)
        if existing is None:
            raise ActivityTypeNotFound(str(type_id))
        if existing.scope is not ActivityTypeScope.PLATFORM:
            # A project's type is the project owner's to edit; the admin surface
            # is read-only over those ([R30.31]).
            raise ActivityTypeNotFound(str(type_id))

        await self._policy.assert_allows(
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            retention_days=retention_days,
        )

        await self._repo.update(
            type_id,
            name=name,
            payload_schema=existing.payload_schema,
            validator_kind=existing.validator_kind,
            validator_config=existing.validator_config,
            retention_days=retention_days,
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            # No behavioral field can change here, so `version` never moves — and
            # the "rejected while an activation is live" rule that guards a
            # behavioral edit does not apply either. A governance flag must stay
            # editable while a class is running; that is the whole point.
            bump_version=False,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=type_id,
                metadata={
                    "scope": ActivityTypeScope.PLATFORM.value,
                    "version_bumped": False,
                    "previous": {
                        "name": existing.name,
                        "retention_days": str(existing.retention_days),
                        "expose_payload_to_agent": str(existing.expose_payload_to_agent),
                        "echo_includes_content": str(existing.echo_includes_content),
                    },
                },
                request_id=request_id,
            ),
        )
        updated = await self._repo.get(type_id)
        if updated is None:  # pragma: no cover — just updated in this transaction
            raise ActivityTypeNotFound(str(type_id))
        return updated

    async def list_types(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        return await self._repo.list_for_project(project_id)

    async def list_owned_types(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        """Types this project owns, as opposed to :meth:`list_types`' usable set."""
        return await self._repo.list_owned_by_project(project_id)

    async def list_platform_types(self) -> Sequence[ActivityType]:
        return await self._repo.list_platform()

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
                metadata={
                    "project_id": str(existing.project_id) if existing.project_id is not None else None,
                    "scope": existing.scope.value,
                    "key": existing.key,
                },
                request_id=request_id,
            ),
        )

    @staticmethod
    def _validate_validator_config(kind: ValidatorKind, config: dict[str, Any]) -> None:
        if kind is ValidatorKind.IN_PROCESS:
            vid = str(config.get("validator_id", ""))
            if not vid or not is_registered(vid):
                raise ValidatorConfigInvalid(f"unknown in-process validator_id {vid!r}")
            # A registered validator may declare required config (e.g. exact_match's
            # field/expected); reject a malformed config here rather than deferring
            # it to a per-submission error verdict.
            config_validator = get_config_validator(vid)
            if config_validator is not None:
                config_validator(config)
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
