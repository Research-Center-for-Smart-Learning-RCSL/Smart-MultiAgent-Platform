"""Prompt-template application service (§29 / R29.10-R29.12).

CRUD per scope plus the merged, source-grouped resolution for a project.
Scope-ownership is verified here so an ``/api/me`` caller cannot mutate an org
or platform template by id (a mismatched scope reads as not-found, never
leaking existence). Role-based AuthZ is at the API boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.application._scoping import resolve_owning_org_id
from contexts.prompt_studio.domain.errors import (
    TemplateLimitReached,
    TemplateNotFound,
    VersionMismatch,
)
from contexts.prompt_studio.domain.models import (
    TEMPLATES_PER_SCOPE_MAX,
    PromptScope,
    PromptTemplate,
    TemplateDraft,
)
from contexts.prompt_studio.infrastructure.repositories import (
    AssistantConfigRepository,
    PromptTemplateRepository,
)
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel import audit


class TemplateService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._templates = PromptTemplateRepository(db)
        self._configs = AssistantConfigRepository(db)
        self._tenancy = TenancyFacade(db)

    async def list_for_scope(
        self,
        scope: PromptScope,
        *,
        org_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> list[PromptTemplate]:
        return await self._templates.list_for_scope(scope, org_id=org_id, user_id=user_id)

    async def create_template(
        self,
        *,
        actor_user_id: uuid.UUID,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> PromptTemplate:
        count = await self._templates.count_for_scope(scope, org_id=org_id, user_id=user_id)
        if count >= TEMPLATES_PER_SCOPE_MAX:
            raise TemplateLimitReached(f"{scope.value}:{count}")
        template = await self._templates.create(
            scope=scope,
            org_id=org_id,
            user_id=user_id,
            name=name,
            description=description,
            body=body,
            position=count,
        )
        await self._emit("prompt_studio.template_created", template, actor_user_id, actor_ip, request_id)
        return template

    async def update_template(
        self,
        *,
        template_id: uuid.UUID,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        expected_version: int,
        draft: TemplateDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> PromptTemplate:
        await self._assert_owned(template_id, scope, org_id=org_id, user_id=user_id)
        values = {
            k: v
            for k, v in {
                "name": draft.name,
                "description": draft.description,
                "body": draft.body,
                "position": draft.position,
            }.items()
            if v is not None
        }
        if not values:
            # Nothing to change, but an empty-body PATCH is not exempt from
            # optimistic concurrency: a stale If-Match must still 412, the
            # same as it would via the repository's UPDATE ... WHERE version=.
            current = await self._templates.get(template_id)
            assert current is not None  # _assert_owned guarantees existence
            if current.version != expected_version:
                raise VersionMismatch(str(template_id))
            return current
        template = await self._templates.update(template_id, expected_version=expected_version, values=values)
        await self._emit("prompt_studio.template_updated", template, actor_user_id, actor_ip, request_id)
        return template

    async def delete_template(
        self,
        *,
        template_id: uuid.UUID,
        scope: PromptScope,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        template = await self._assert_owned(template_id, scope, org_id=org_id, user_id=user_id)
        await self._templates.delete(template_id)
        await self._emit("prompt_studio.template_deleted", template, actor_user_id, actor_ip, request_id)

    async def _assert_owned(
        self,
        template_id: uuid.UUID,
        scope: PromptScope,
        *,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> PromptTemplate:
        template = await self._templates.get(template_id)
        if template is None or template.scope is not scope:
            raise TemplateNotFound(str(template_id))
        if scope is PromptScope.ORG and template.org_id != org_id:
            raise TemplateNotFound(str(template_id))
        if scope is PromptScope.USER and template.user_id != user_id:
            raise TemplateNotFound(str(template_id))
        return template

    # -- merged resolution for a project (R29.11) ----------------------------

    async def resolve_for_project(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> list[PromptTemplate]:
        """Merged, source-grouped template list for *user_id* in *project_id*.

        Platform templates (unless the owning org hides them) + org templates
        (org-owned projects) + the requesting user's personal templates. Each
        returned template carries its own ``scope`` so the API groups by source.
        """
        merged: list[PromptTemplate] = []
        owning_org_id = await resolve_owning_org_id(self._tenancy, project_id)

        hide_platform = False
        if owning_org_id is not None:
            org_cfg = await self._configs.get_by_scope(PromptScope.ORG, org_id=owning_org_id)
            hide_platform = org_cfg.hide_platform_templates if org_cfg is not None else False

        if not hide_platform:
            merged.extend(await self._templates.list_for_scope(PromptScope.PLATFORM))
        if owning_org_id is not None:
            merged.extend(await self._templates.list_for_scope(PromptScope.ORG, org_id=owning_org_id))
        merged.extend(await self._templates.list_for_scope(PromptScope.USER, user_id=user_id))
        return merged

    async def _emit(
        self,
        action: str,
        template: PromptTemplate,
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
                resource_type="prompt_template",
                resource_id=template.id,
                metadata={"scope": template.scope.value, "name": template.name},
                request_id=request_id,
            ),
        )


__all__ = ["TemplateService"]
