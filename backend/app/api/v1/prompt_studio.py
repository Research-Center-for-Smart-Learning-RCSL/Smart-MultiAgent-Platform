"""`/api/**/prompt-assistant` + `/api/**/prompt-templates` — §29.

Three configuration scopes share one presentational contract:
- ``/api/me/...``            self-service, any verified user (own scope)
- ``/api/orgs/{org_id}/...`` Org Owner (PROMPT_STUDIO_ORG_MANAGE)
- ``/api/admin/...``         platform, ``require_admin``

Plus project-scoped resolved reads (``require_membership``) and the streaming
session endpoints. Routers stay thin over the application services; key
material is never returned (metadata only, R29.14).
"""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Header,
    HTTPException,
    Path,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.api.v1.deps import parse_if_match
from contexts.keys.interfaces.facade import KeysFacade
from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.application.file_service import FileService
from contexts.prompt_studio.application.session_service import SessionService
from contexts.prompt_studio.application.template_service import TemplateService
from contexts.prompt_studio.domain.errors import AssistantConfigNotFound
from contexts.prompt_studio.domain.models import (
    MAX_USER_MESSAGE_CHARS,
    SYSTEM_PROMPT_MAX,
    TEMPLATE_BODY_MAX,
    TEMPLATE_DESC_MAX,
    TEMPLATE_NAME_MAX,
    AssistantConfig,
    AssistantFile,
    PromptScope,
    PromptTemplate,
    TemplateDraft,
)
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage import get_minio_client

_MAX_REFERENCE_UPLOAD = 5 * 1024 * 1024

me_router = APIRouter(prefix="/api/me", tags=["prompt-studio"])
org_router = APIRouter(prefix="/api/orgs/{org_id}", tags=["prompt-studio"])
admin_router = APIRouter(prefix="/api/admin", tags=["prompt-studio"])
project_router = APIRouter(prefix="/api/projects/{project_id}", tags=["prompt-studio"])
session_router = APIRouter(prefix="/api/prompt-assistant", tags=["prompt-studio"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class KeyMetaOut(BaseModel):
    id: uuid.UUID
    provider: str
    name: str
    masked_preview: str


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    mime: str
    scan_status: str
    extracted_chars: int
    created_at: str


class AssistantConfigOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    scope: str
    enabled: bool
    system_prompt: str
    key_id: uuid.UUID | None
    key: KeyMetaOut | None
    key_revoked: bool
    model_id: str | None
    daily_request_limit_per_user: int
    hide_platform_templates: bool
    version: int
    files: list[FileOut]


class ConfigEnvelopeOut(BaseModel):
    configured: bool
    config: AssistantConfigOut | None


class AssistantConfigPutIn(BaseModel):
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    system_prompt: str = Field(default="", max_length=SYSTEM_PROMPT_MAX)
    key_id: uuid.UUID | None = None
    model_id: str | None = Field(default=None, max_length=200)
    daily_request_limit_per_user: int = Field(default=50, ge=1, le=100_000)
    enabled: bool = False
    hide_platform_templates: bool = False


class TemplateOut(BaseModel):
    id: uuid.UUID
    scope: str
    name: str
    description: str
    body: str
    position: int
    version: int
    created_at: str
    updated_at: str


class TemplateCreateIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=TEMPLATE_NAME_MAX)
    description: str = Field(default="", max_length=TEMPLATE_DESC_MAX)
    body: str = Field(default="", max_length=TEMPLATE_BODY_MAX)


class TemplatePatchIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=TEMPLATE_NAME_MAX)
    description: str | None = Field(default=None, max_length=TEMPLATE_DESC_MAX)
    body: str | None = Field(default=None, max_length=TEMPLATE_BODY_MAX)
    position: int | None = Field(default=None, ge=0)


class ResolvedAssistantOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    available: bool
    source_scope: str | None
    model_id: str | None


class SessionCreatedOut(BaseModel):
    session_id: uuid.UUID


class SessionMessageOut(BaseModel):
    role: str
    content: str
    error: bool


class AssistantSessionOut(BaseModel):
    session_id: uuid.UUID
    messages: list[SessionMessageOut]


class MessageIn(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1, max_length=MAX_USER_MESSAGE_CHARS)
    editor_draft: str | None = Field(default=None, max_length=TEMPLATE_BODY_MAX)


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


def _file_out(f: AssistantFile) -> FileOut:
    return FileOut(
        id=f.id,
        filename=f.filename,
        size_bytes=f.size_bytes,
        mime=f.mime,
        scan_status=f.scan_status.value,
        extracted_chars=f.extracted_chars,
        created_at=f.created_at.isoformat(),
    )


def _template_out(t: PromptTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        scope=t.scope.value,
        name=t.name,
        description=t.description,
        body=t.body,
        position=t.position,
        version=t.version,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
    )


async def _config_out(db: AsyncSession, config: AssistantConfig) -> AssistantConfigOut:
    files = await ConfigService(db).list_files(config.id)
    key_meta: KeyMetaOut | None = None
    key_revoked = False
    if config.key_id is not None:
        key = await KeysFacade(db).get_key(config.key_id)
        if key is None:
            key_revoked = True
        else:
            key_meta = KeyMetaOut(
                id=key.id,
                provider=key.provider.value,
                name=key.name,
                masked_preview=key.masked_preview,
            )
    return AssistantConfigOut(
        scope=config.scope.value,
        enabled=config.enabled,
        system_prompt=config.system_prompt,
        key_id=config.key_id,
        key=key_meta,
        key_revoked=key_revoked,
        model_id=config.model_id,
        daily_request_limit_per_user=config.daily_request_limit_per_user,
        hide_platform_templates=config.hide_platform_templates,
        version=config.version,
        files=[_file_out(f) for f in files],
    )


def _require_verified(principal: Principal) -> None:
    if not principal.email_verified:
        raise HTTPException(status_code=403, detail="email verification required")


# ---------------------------------------------------------------------------
# Shared scope-parametrised handlers
# ---------------------------------------------------------------------------


async def _get_config(
    db: AsyncSession, scope: PromptScope, *, org_id=None, user_id=None
) -> ConfigEnvelopeOut:
    config = await ConfigService(db).get_config(scope, org_id=org_id, user_id=user_id)
    if config is None:
        return ConfigEnvelopeOut(configured=False, config=None)
    return ConfigEnvelopeOut(configured=True, config=await _config_out(db, config))


async def _put_config(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    body: AssistantConfigPutIn,
    if_match: str | None,
    *,
    org_id=None,
    user_id=None,
) -> AssistantConfigOut:
    config = await ConfigService(db).put_config(
        actor_user_id=principal.user_id,
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        system_prompt=body.system_prompt,
        key_id=body.key_id,
        model_id=body.model_id,
        daily_request_limit_per_user=body.daily_request_limit_per_user,
        enabled=body.enabled,
        hide_platform_templates=body.hide_platform_templates,
        expected_version=parse_if_match(if_match),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return await _config_out(db, config)


async def _upload_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    file: UploadFile,
    *,
    org_id=None,
    user_id=None,
) -> FileOut:
    config = await ConfigService(db).get_config(scope, org_id=org_id, user_id=user_id)
    if config is None:
        raise AssistantConfigNotFound(f"{scope.value} config must be created before uploading files")
    if file.size is not None and file.size > _MAX_REFERENCE_UPLOAD:
        raise HTTPException(status_code=413, detail="file exceeds 5 MB limit")
    data = await file.read()
    result = await FileService(db, get_minio_client()).upload_reference_file(
        config_id=config.id,
        filename=file.filename or "file",
        data=data,
        mime=file.content_type or "application/octet-stream",
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _file_out(result)


async def _delete_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    file_id: uuid.UUID,
    *,
    org_id=None,
    user_id=None,
) -> None:
    config = await ConfigService(db).get_config(scope, org_id=org_id, user_id=user_id)
    if config is None:
        raise AssistantConfigNotFound(str(scope.value))
    await FileService(db, get_minio_client()).remove_reference_file(
        config_id=config.id,
        file_id=file_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


async def _list_templates(
    db: AsyncSession, scope: PromptScope, *, org_id=None, user_id=None
) -> list[TemplateOut]:
    rows = await TemplateService(db).list_for_scope(scope, org_id=org_id, user_id=user_id)
    return [_template_out(t) for t in rows]


async def _create_template(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    body: TemplateCreateIn,
    *,
    org_id=None,
    user_id=None,
) -> TemplateOut:
    template = await TemplateService(db).create_template(
        actor_user_id=principal.user_id,
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        name=body.name,
        description=body.description,
        body=body.body,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _template_out(template)


async def _patch_template(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    template_id: uuid.UUID,
    body: TemplatePatchIn,
    if_match: str,
    *,
    org_id=None,
    user_id=None,
) -> TemplateOut:
    expected = parse_if_match(if_match)
    assert expected is not None  # If-Match header is required on PATCH
    template = await TemplateService(db).update_template(
        template_id=template_id,
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        expected_version=expected,
        draft=TemplateDraft(
            name=body.name, description=body.description, body=body.body, position=body.position
        ),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _template_out(template)


async def _delete_template_scoped(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: PromptScope,
    template_id: uuid.UUID,
    *,
    org_id=None,
    user_id=None,
) -> None:
    await TemplateService(db).delete_template(
        template_id=template_id,
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# ---------------------------------------------------------------------------
# /api/me — personal scope (verified user, own resources)
# ---------------------------------------------------------------------------


@me_router.get("/prompt-assistant/config")
async def me_get_config(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ConfigEnvelopeOut:
    _require_verified(principal)
    return await _get_config(db, PromptScope.USER, user_id=principal.user_id)


@me_router.put("/prompt-assistant/config")
async def me_put_config(
    body: AssistantConfigPutIn,
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AssistantConfigOut:
    _require_verified(principal)
    return await _put_config(db, ctx, principal, PromptScope.USER, body, if_match, user_id=principal.user_id)


@me_router.post("/prompt-assistant/config/files", status_code=status.HTTP_201_CREATED)
async def me_upload_file(
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> FileOut:
    _require_verified(principal)
    return await _upload_file(db, ctx, principal, PromptScope.USER, file, user_id=principal.user_id)


@me_router.delete(
    "/prompt-assistant/config/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def me_delete_file(
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    _require_verified(principal)
    await _delete_file(db, ctx, principal, PromptScope.USER, file_id, user_id=principal.user_id)


@me_router.get("/prompt-templates")
async def me_list_templates(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[TemplateOut]:
    _require_verified(principal)
    return await _list_templates(db, PromptScope.USER, user_id=principal.user_id)


@me_router.post("/prompt-templates", status_code=status.HTTP_201_CREATED)
async def me_create_template(
    body: TemplateCreateIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    _require_verified(principal)
    return await _create_template(db, ctx, principal, PromptScope.USER, body, user_id=principal.user_id)


@me_router.patch("/prompt-templates/{template_id}")
async def me_patch_template(
    body: TemplatePatchIn,
    template_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    _require_verified(principal)
    return await _patch_template(
        db, ctx, principal, PromptScope.USER, template_id, body, if_match, user_id=principal.user_id
    )


@me_router.delete(
    "/prompt-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def me_delete_template(
    template_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    _require_verified(principal)
    await _delete_template_scoped(
        db, ctx, principal, PromptScope.USER, template_id, user_id=principal.user_id
    )


# ---------------------------------------------------------------------------
# /api/orgs/{org_id} — org scope (Org Owner)
# ---------------------------------------------------------------------------

_ORG_GUARD = Depends(require(Capability.PROMPT_STUDIO_ORG_MANAGE, scope_from_path(org_param="org_id")))


@org_router.get("/prompt-assistant/config", dependencies=[_ORG_GUARD])
async def org_get_config(
    org_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> ConfigEnvelopeOut:
    return await _get_config(db, PromptScope.ORG, org_id=org_id)


@org_router.put("/prompt-assistant/config", dependencies=[_ORG_GUARD])
async def org_put_config(
    body: AssistantConfigPutIn,
    org_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AssistantConfigOut:
    return await _put_config(db, ctx, principal, PromptScope.ORG, body, if_match, org_id=org_id)


@org_router.post(
    "/prompt-assistant/config/files", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD]
)
async def org_upload_file(
    org_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> FileOut:
    return await _upload_file(db, ctx, principal, PromptScope.ORG, file, org_id=org_id)


@org_router.delete(
    "/prompt-assistant/config/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[_ORG_GUARD],
)
async def org_delete_file(
    org_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_file(db, ctx, principal, PromptScope.ORG, file_id, org_id=org_id)


@org_router.get("/prompt-templates", dependencies=[_ORG_GUARD])
async def org_list_templates(
    org_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> list[TemplateOut]:
    return await _list_templates(db, PromptScope.ORG, org_id=org_id)


@org_router.post("/prompt-templates", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD])
async def org_create_template(
    body: TemplateCreateIn,
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    return await _create_template(db, ctx, principal, PromptScope.ORG, body, org_id=org_id)


@org_router.patch("/prompt-templates/{template_id}", dependencies=[_ORG_GUARD])
async def org_patch_template(
    body: TemplatePatchIn,
    org_id: uuid.UUID = Path(...),
    template_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    return await _patch_template(
        db, ctx, principal, PromptScope.ORG, template_id, body, if_match, org_id=org_id
    )


@org_router.delete(
    "/prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[_ORG_GUARD],
)
async def org_delete_template(
    org_id: uuid.UUID = Path(...),
    template_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_template_scoped(db, ctx, principal, PromptScope.ORG, template_id, org_id=org_id)


# ---------------------------------------------------------------------------
# /api/admin — platform scope
# ---------------------------------------------------------------------------


@admin_router.get("/prompt-assistant/config")
async def admin_get_config(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> ConfigEnvelopeOut:
    return await _get_config(db, PromptScope.PLATFORM)


@admin_router.put("/prompt-assistant/config")
async def admin_put_config(
    body: AssistantConfigPutIn,
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> AssistantConfigOut:
    return await _put_config(db, ctx, principal, PromptScope.PLATFORM, body, if_match)


@admin_router.post("/prompt-assistant/config/files", status_code=status.HTTP_201_CREATED)
async def admin_upload_file(
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> FileOut:
    return await _upload_file(db, ctx, principal, PromptScope.PLATFORM, file)


@admin_router.delete(
    "/prompt-assistant/config/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def admin_delete_file(
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_file(db, ctx, principal, PromptScope.PLATFORM, file_id)


@admin_router.get("/prompt-templates")
async def admin_list_templates(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[TemplateOut]:
    return await _list_templates(db, PromptScope.PLATFORM)


@admin_router.post("/prompt-templates", status_code=status.HTTP_201_CREATED)
async def admin_create_template(
    body: TemplateCreateIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    return await _create_template(db, ctx, principal, PromptScope.PLATFORM, body)


@admin_router.patch("/prompt-templates/{template_id}")
async def admin_patch_template(
    body: TemplatePatchIn,
    template_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> TemplateOut:
    return await _patch_template(db, ctx, principal, PromptScope.PLATFORM, template_id, body, if_match)


@admin_router.delete(
    "/prompt-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def admin_delete_template(
    template_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_template_scoped(db, ctx, principal, PromptScope.PLATFORM, template_id)


# ---------------------------------------------------------------------------
# /api/projects/{project_id} — resolved reads (members)
# ---------------------------------------------------------------------------


@project_router.get(
    "/prompt-assistant", dependencies=[Depends(require_membership(project_param="project_id"))]
)
async def project_resolved_assistant(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ResolvedAssistantOut:
    config = await ConfigService(db).resolve_for_project(project_id=project_id, user_id=principal.user_id)
    if config is None or config.key_id is None:
        return ResolvedAssistantOut(available=False, source_scope=None, model_id=None)
    return ResolvedAssistantOut(available=True, source_scope=config.scope.value, model_id=config.model_id)


@project_router.get(
    "/prompt-templates", dependencies=[Depends(require_membership(project_param="project_id"))]
)
async def project_merged_templates(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[TemplateOut]:
    rows = await TemplateService(db).resolve_for_project(project_id=project_id, user_id=principal.user_id)
    return [_template_out(t) for t in rows]


@project_router.post(
    "/prompt-assistant/sessions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_membership(project_param="project_id"))],
)
async def create_session(
    project_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SessionCreatedOut:
    session = await SessionService(db).create_session(user_id=principal.user_id, project_id=project_id)
    return SessionCreatedOut(session_id=session.session_id)


# ---------------------------------------------------------------------------
# /api/prompt-assistant/sessions/{session_id} — recovery read (§29, F-13 fix)
# ---------------------------------------------------------------------------


@session_router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AssistantSessionOut:
    # Ownership-only (Q-4): require_owned_session collapses "no such session"
    # and "wrong owner" into one 404 — no membership check, no admin bypass.
    session = await SessionService(db).require_owned_session(session_id, principal.user_id)
    return AssistantSessionOut(
        session_id=session.session_id,
        messages=[SessionMessageOut(role=m.role, content=m.content, error=m.error) for m in session.messages],
    )


# ---------------------------------------------------------------------------
# /api/prompt-assistant/sessions/{session_id}/messages — enqueue a turn
# ---------------------------------------------------------------------------


@session_router.post(
    "/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED, response_model=None
)
async def post_message(
    body: MessageIn = Body(...),
    session_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await SessionService(db).post_message(
        session_id=session_id,
        actor_user_id=principal.user_id,
        content=body.content,
        editor_draft=body.editor_draft,
    )


__all__ = ["admin_router", "me_router", "org_router", "project_router", "session_router"]
