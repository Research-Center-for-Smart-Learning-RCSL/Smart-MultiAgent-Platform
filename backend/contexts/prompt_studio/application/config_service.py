"""Assistant-config application service (§29 / R29.02-R29.06).

Owns config CRUD, the pinned-key ownership + capability invariants, and the
effective-config resolution chain (user -> org -> platform). Role-based AuthZ
is enforced at the API boundary via ``require(...)``/``require_admin``; this
service enforces the invariants that are not role-based (a configurer may only
pin a key they own, and it must support chat) plus scope integrity.

Never commits — the ``db_session`` dependency owns the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.interfaces.facade import KeysFacade, ProviderCapability
from contexts.prompt_studio.domain.errors import (
    AssistantConfigNotFound,
    PinnedKeyCapabilityMismatch,
    PinnedKeyNotOwned,
    VersionMismatch,
)
from contexts.prompt_studio.domain.models import AssistantConfig, PromptScope
from contexts.prompt_studio.infrastructure.repositories import AssistantConfigRepository
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel import audit


class ConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = AssistantConfigRepository(db)
        self._keys = KeysFacade(db)
        self._tenancy = TenancyFacade(db)

    async def get_config(
        self,
        scope: PromptScope,
        *,
        org_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> AssistantConfig | None:
        return await self._configs.get_by_scope(scope, org_id=org_id, user_id=user_id)

    async def put_config(
        self,
        *,
        actor_user_id: uuid.UUID,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        system_prompt: str,
        key_id: uuid.UUID | None,
        model_id: str | None,
        daily_request_limit_per_user: int,
        enabled: bool,
        hide_platform_templates: bool,
        expected_version: int | None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> AssistantConfig:
        """Create-or-replace the config for a scope holder.

        A pinned key must be owned by the configurer and support chat. When a
        config already exists, ``expected_version`` (from ``If-Match``) is
        required and enforced for optimistic concurrency.
        """
        if key_id is not None:
            await self._assert_key_usable(actor_user_id=actor_user_id, key_id=key_id)

        existing = await self._configs.get_by_scope(scope, org_id=org_id, user_id=user_id)
        # hide_platform_templates is meaningful only for the org scope.
        values = {
            "system_prompt": system_prompt,
            "key_id": key_id,
            "model_id": model_id,
            "daily_request_limit_per_user": daily_request_limit_per_user,
            "enabled": enabled,
            "hide_platform_templates": hide_platform_templates if scope is PromptScope.ORG else False,
        }

        if existing is None:
            config = await self._configs.create(scope=scope, org_id=org_id, user_id=user_id, values=values)
            action = "prompt_studio.config_created"
        else:
            if expected_version is None:
                raise VersionMismatch(str(existing.id))
            config = await self._configs.update(existing.id, expected_version=expected_version, values=values)
            action = "prompt_studio.config_updated"

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="prompt_assistant_config",
                resource_id=config.id,
                metadata={"scope": scope.value, "enabled": str(enabled)},
                request_id=request_id,
            ),
        )
        return config

    async def _assert_key_usable(self, *, actor_user_id: uuid.UUID, key_id: uuid.UUID) -> None:
        key = await self._keys.get_key(key_id)
        if key is None or key.owner_user_id != actor_user_id:
            raise PinnedKeyNotOwned(str(key_id))
        if not await self._keys.validate_key_capability(key_id, ProviderCapability.LLM_CHAT):
            raise PinnedKeyCapabilityMismatch(str(key_id))

    # -- effective-config resolution (R29.04) --------------------------------

    async def resolve_for_project(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> AssistantConfig | None:
        """Resolve the effective enabled config for *user_id* working in *project_id*.

        Chain: the user's enabled personal config -> the owning org's enabled
        config (org-owned projects only) -> the enabled platform config -> None.
        Disabled configs are skipped (fall through to the next scope).
        """
        personal = await self._configs.get_by_scope(PromptScope.USER, user_id=user_id)
        if personal is not None and personal.enabled:
            return personal

        project = await self._tenancy.get_project(project_id)
        if project is not None and project.owner_org_id is not None:
            org_cfg = await self._configs.get_by_scope(PromptScope.ORG, org_id=project.owner_org_id)
            if org_cfg is not None and org_cfg.enabled:
                return org_cfg

        platform = await self._configs.get_by_scope(PromptScope.PLATFORM)
        if platform is not None and platform.enabled:
            return platform
        return None

    async def get_config_or_raise(self, config_id: uuid.UUID) -> AssistantConfig:
        config = await self._configs.get_by_id(config_id)
        if config is None:
            raise AssistantConfigNotFound(str(config_id))
        return config


__all__ = ["ConfigService"]
