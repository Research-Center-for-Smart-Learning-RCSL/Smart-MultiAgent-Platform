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
from contexts.prompt_studio.application._scoping import resolve_owning_org_id
from contexts.prompt_studio.domain.errors import (
    AssistantConfigNotFound,
    PinnedKeyCapabilityMismatch,
    PinnedKeyNotOwned,
    VersionMismatch,
)
from contexts.prompt_studio.domain.models import AssistantConfig, AssistantFile, PromptScope
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
        persona_prompt: str,
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
        """Create-or-replace the singleton config for an org/user scope holder.

        A pinned key must be owned by the configurer and support chat. When a
        config already exists, ``expected_version`` (from ``If-Match``) is
        required and enforced for optimistic concurrency. Platform-scope
        presets are managed separately (``create_preset`` / `update_preset`),
        since platform scope is no longer a singleton (R29.02 relaxed).
        """
        if key_id is not None:
            await self._assert_key_usable(actor_user_id=actor_user_id, key_id=key_id)

        existing = await self._configs.get_by_scope(scope, org_id=org_id, user_id=user_id)
        # hide_platform_templates is meaningful only for the org scope.
        values = {
            "persona_prompt": persona_prompt,
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

        owning_org_id = await resolve_owning_org_id(self._tenancy, project_id)
        if owning_org_id is not None:
            org_cfg = await self._configs.get_by_scope(PromptScope.ORG, org_id=owning_org_id)
            if org_cfg is not None and org_cfg.enabled:
                return org_cfg

        return await self._configs.get_enabled_platform_preset()

    async def list_files(self, config_id: uuid.UUID) -> list[AssistantFile]:
        """Metadata only -- the API layer's response DTOs never serialize extracted_text."""
        return await self._configs.list_files_meta(config_id)

    async def get_config_or_raise(self, config_id: uuid.UUID) -> AssistantConfig:
        config = await self._configs.get_by_id(config_id)
        if config is None:
            raise AssistantConfigNotFound(str(config_id))
        return config

    # -- platform preset CRUD (R29.16) ---------------------------------------
    #
    # Platform scope is the only scope that isn't a singleton: it holds
    # multiple named presets. At most one may be enabled at a time (DB-enforced
    # by uq_prompt_assistant_config_platform_active); enabling one here
    # disables any other currently-enabled preset first, so the caller never
    # has to turn the old one off by hand and resolve_for_project's
    # get_enabled_platform_preset() always sees at most one candidate.

    async def list_platform_presets(self) -> list[AssistantConfig]:
        return await self._configs.list_platform()

    async def create_preset(
        self,
        *,
        actor_user_id: uuid.UUID,
        name: str,
        description: str,
        persona_prompt: str,
        system_prompt: str,
        key_id: uuid.UUID | None,
        model_id: str | None,
        daily_request_limit_per_user: int,
        enabled: bool,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> AssistantConfig:
        if key_id is not None:
            await self._assert_key_usable(actor_user_id=actor_user_id, key_id=key_id)
        if enabled:
            await self._disable_other_enabled_presets(except_id=None)
        preset = await self._configs.create(
            scope=PromptScope.PLATFORM,
            org_id=None,
            user_id=None,
            values={
                "name": name,
                "description": description,
                "persona_prompt": persona_prompt,
                "system_prompt": system_prompt,
                "key_id": key_id,
                "model_id": model_id,
                "daily_request_limit_per_user": daily_request_limit_per_user,
                "enabled": enabled,
                "hide_platform_templates": False,
            },
        )
        await self._emit_preset("prompt_studio.preset_created", preset, actor_user_id, actor_ip, request_id)
        return preset

    async def update_preset(
        self,
        *,
        preset_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        expected_version: int,
        name: str,
        description: str,
        persona_prompt: str,
        system_prompt: str,
        key_id: uuid.UUID | None,
        model_id: str | None,
        daily_request_limit_per_user: int,
        enabled: bool,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> AssistantConfig:
        if key_id is not None:
            await self._assert_key_usable(actor_user_id=actor_user_id, key_id=key_id)
        if enabled:
            await self._disable_other_enabled_presets(except_id=preset_id)
        preset = await self._configs.update(
            preset_id,
            expected_version=expected_version,
            values={
                "name": name,
                "description": description,
                "persona_prompt": persona_prompt,
                "system_prompt": system_prompt,
                "key_id": key_id,
                "model_id": model_id,
                "daily_request_limit_per_user": daily_request_limit_per_user,
                "enabled": enabled,
            },
        )
        await self._emit_preset("prompt_studio.preset_updated", preset, actor_user_id, actor_ip, request_id)
        return preset

    async def delete_preset(
        self,
        *,
        preset_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        preset = await self.get_config_or_raise(preset_id)
        await self._configs.delete(preset_id)
        await self._emit_preset("prompt_studio.preset_deleted", preset, actor_user_id, actor_ip, request_id)

    async def _disable_other_enabled_presets(self, *, except_id: uuid.UUID | None) -> None:
        for other in await self._configs.list_platform():
            if other.enabled and other.id != except_id:
                await self._configs.update(
                    other.id, expected_version=other.version, values={"enabled": False}
                )

    async def _emit_preset(
        self,
        action: str,
        preset: AssistantConfig,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="prompt_assistant_config",
                resource_id=preset.id,
                metadata={"scope": "platform", "name": preset.name, "enabled": str(preset.enabled)},
                request_id=request_id,
            ),
        )


__all__ = ["ConfigService"]
