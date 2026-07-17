"""`/api/**/skills` + `/api/agents/{id}/skill-bindings` — §31.

Four scope routers share one presentational contract, and **scope is a literal per call
site, never read from a request body** — a body-supplied scope would let any caller
who can reach one router write into another's rows:

- ``/api/agents/{agent_id}/skills``     RESOURCE_CREATE_EDIT at the agent's project
- ``/api/projects/{project_id}/skills`` RESOURCE_CREATE_EDIT at the project
- ``/api/orgs/{org_id}/skills``         SKILL_ORG_MANAGE (Org Owner)
- ``/api/admin/skills``                 platform, ``require_admin``

Bindings live under a **separate path** (`/api/agents/{id}/skill-bindings/{skill_id}`)
rather than under `/skills/{skill_id}`. The reviewed design collided `DELETE
/api/agents/{id}/skills/{skill_id}` between "soft-delete this agent-scoped skill" and
"unbind this skill from the agent"; FastAPI would resolve one and silently make the other
unreachable, so an operator unbinding one skill would soft-delete it — and the delete
then unbinds it from every agent in the tenant.

The router-level capability proves the caller may manage skills *in this scope*; it never
proves the quoted skill *is* in that scope. `skill_service._assert_owned` is that control
(404, never 403), and `binding_service.resolve_bindable` is the control for the bind
endpoint, which has no scope in its path at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.api.v1.deps import PaginationParams, assert_project_membership, parse_if_match
from contexts.skills.domain.models import (
    MAX_DESCRIPTION_CHARS,
    SKILL_NAME_RE,
    Skill,
    SkillDraft,
    SkillFile,
    SkillScope,
)
from contexts.skills.domain.text_rules import (
    MAX_FILE_PATH_CHARS,
    skill_file_path_reason,
    text_rejection_reason,
)
from contexts.skills.interfaces.facade import MAX_SKILL_FILE_BYTES, SkillsFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

# API-7: an unbounded body lets a single request drive memory and DB load. The body is
# the SKILL.md the model reads; 256 KiB is far above any real skill and far below a
# denial-of-service.
_MAX_BODY = 256 * 1024
_MAX_LIST_ITEMS = 64
_MAX_TOOL_NAME = 200

agent_router = APIRouter(prefix="/api/agents/{agent_id}/skills", tags=["skills"])
project_router = APIRouter(prefix="/api/projects/{project_id}/skills", tags=["skills"])
org_router = APIRouter(prefix="/api/orgs/{org_id}/skills", tags=["skills"])
admin_router = APIRouter(prefix="/api/admin/skills", tags=["skills"])
binding_router = APIRouter(prefix="/api/agents/{agent_id}/skill-bindings", tags=["skills"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def _validate_text(field: str, value: str, *, max_chars: int) -> str:
    reason = text_rejection_reason(value, max_chars=max_chars)
    if reason is not None:
        raise ValueError(f"{field} {reason}")
    return value


def _validate_names(field: str, values: list[str]) -> list[str]:
    if len(values) > _MAX_LIST_ITEMS:
        raise ValueError(f"{field} exceeds {_MAX_LIST_ITEMS} entries")
    for v in values:
        _validate_text(field, v, max_chars=_MAX_TOOL_NAME)
    return values


class SkillCreateIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    description: str
    body: str = Field(default="", max_length=_MAX_BODY)
    requires: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        # The charset rules run first so an over-length or delimiter-bearing name gets a
        # reason rather than a bare pattern mismatch; the pattern is what actually
        # constrains it, since `name` is also the directory under /workspace/skills/.
        _validate_text("name", v, max_chars=64)
        if not SKILL_NAME_RE.match(v):
            raise ValueError("name must be lowercase alphanumeric with hyphens, 1-64 chars")
        return v

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        return _validate_text("description", v, max_chars=MAX_DESCRIPTION_CHARS)

    @field_validator("requires", "allowed_tools")
    @classmethod
    def _check_lists(cls, v: list[str]) -> list[str]:
        return _validate_names("tool name", v)


class SkillPatchIn(BaseModel):
    """Every field optional; missing means unchanged.

    `name` is **absent by construction, not optional**: it is the key the model invokes,
    the key bindings and bundle exports are addressed by, and the directory name under
    /workspace/skills/. Renaming is a copy (Q-11/AC-39). With `extra="forbid"` a client
    that sends `name` gets a 422 rather than a silent no-op — which is the point: a
    rename that appears to work but does not is worse than a rejection.
    """

    model_config = {"extra": "forbid"}

    description: str | None = None
    body: str | None = Field(default=None, max_length=_MAX_BODY)
    requires: list[str] | None = None
    allowed_tools: list[str] | None = None

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str | None) -> str | None:
        return v if v is None else _validate_text("description", v, max_chars=MAX_DESCRIPTION_CHARS)

    @field_validator("requires", "allowed_tools")
    @classmethod
    def _check_lists(cls, v: list[str] | None) -> list[str] | None:
        return v if v is None else _validate_names("tool name", v)


class SkillCopyIn(BaseModel):
    model_config = {"extra": "forbid"}

    target_scope: SkillScope
    target_owner_id: uuid.UUID | None = None
    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        _validate_text("name", v, max_chars=64)
        if not SKILL_NAME_RE.match(v):
            raise ValueError("name must be lowercase alphanumeric with hyphens, 1-64 chars")
        return v


class SkillSummaryOut(BaseModel):
    """A skill without its body — what a listing needs.

    The body is bounded only by `_MAX_BODY` (256 KiB) and `limit` reaches 500, so
    including it here would let one request return ~128 MB to render a menu of names. It
    is served by the detail endpoint, one skill at a time, to the caller who asked for it.
    """

    id: uuid.UUID
    scope: SkillScope
    owner_id: uuid.UUID | None
    name: str
    description: str
    body_sha256: str
    source: str
    bundle_sha256: str | None
    diverged: bool
    requires: list[str]
    allowed_tools: list[str]
    created_by: uuid.UUID | None
    version: int
    created_at: str
    deleted_at: str | None


class SkillOut(SkillSummaryOut):
    body: str


class SkillPageOut(BaseModel):
    items: list[SkillSummaryOut]
    total: int


class SkillBindingOut(BaseModel):
    agent_id: uuid.UUID
    skill_id: uuid.UUID
    name: str


def _validate_file_path(value: str) -> str:
    reason = skill_file_path_reason(value)
    if reason is not None:
        raise ValueError(f"path {reason}")
    return value


class SkillFileCreateIn(BaseModel):
    """A UI-authored file (AC-16).

    `kind` is **absent by construction**, not optional: R31.18 derives it from the path's
    top-level directory, and a client-chosen kind would let an uploader put a script under
    `assets/` and have it staged, or mark a binary `reference` and have `read_skill`
    render its bytes into the prompt. With `extra="forbid"` a client that sends one gets a
    422 rather than a silent no-op.
    """

    model_config = {"extra": "forbid"}

    path: str = Field(min_length=1, max_length=MAX_FILE_PATH_CHARS)
    content: str = Field(max_length=_MAX_BODY)
    mime: str = Field(default="text/plain", max_length=_MAX_TOOL_NAME)

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        return _validate_file_path(v)


class SkillFilePatchIn(BaseModel):
    """New text for an existing file.

    `path` is absent for the same reason `SkillPatchIn` omits `name`: it is the key
    `SKILL.md` references the file by, so a rename is a delete plus an add, not an edit.
    """

    model_config = {"extra": "forbid"}

    content: str = Field(max_length=_MAX_BODY)


class SkillFileOut(BaseModel):
    """One bundled file's metadata.

    There is deliberately no per-file `readable` flag. AC-34's gate is whole-skill
    (Q-18), so a badge saying one file is fine would contradict the skill it belongs to
    being unreadable; the UI derives that state from the collection's scan statuses.
    """

    id: uuid.UUID
    skill_id: uuid.UUID
    path: str
    kind: str
    mime: str
    size_bytes: int
    sha256: str
    scan_status: str
    extracted_chars: int
    created_at: str


def _file_out(f: SkillFile) -> SkillFileOut:
    # `minio_key` is deliberately not exposed: it is the object path, and the client has
    # no bucket access. Publishing it would leak the storage layout for nothing.
    return SkillFileOut(
        id=f.id,
        skill_id=f.skill_id,
        path=f.path,
        kind=f.kind.value,
        mime=f.mime,
        size_bytes=f.size_bytes,
        sha256=f.sha256,
        scan_status=f.scan_status.value,
        extracted_chars=f.extracted_chars,
        created_at=f.created_at.isoformat(),
    )


class SkillScopeCountsOut(BaseModel):
    """[R31.11] — live skills per scope, keyed by the scope's wire value.

    A map rather than four named fields: the scope set is the domain enum's, and four
    hand-written columns would drift from it silently the day a fifth scope lands
    (FU-14 already tracks one).
    """

    counts: dict[str, int]
    total: int


def _summary_fields(s: Skill) -> dict[str, Any]:
    return {
        "id": s.id,
        "scope": s.scope,
        "owner_id": s.owner_id,
        "name": s.name,
        "description": s.description,
        "body_sha256": s.body_sha256,
        "source": s.source.value,
        "bundle_sha256": s.bundle_sha256,
        # Q-20's badge is derived here rather than on the model, and it is honestly False
        # for now: divergence is `hash(current authored byte set) != bundle_sha256`, and
        # the authored byte set is defined by the exporter, which lands with bundles. No
        # row carries a bundle_sha256 until an importer exists, so anything else this
        # could compute would badge every imported skill as diverged forever.
        "diverged": False,
        "requires": list(s.requires),
        "allowed_tools": list(s.allowed_tools),
        "created_by": s.created_by,
        "version": s.version,
        "created_at": s.created_at.isoformat(),
        "deleted_at": s.deleted_at.isoformat() if s.deleted_at else None,
    }


def _skill_out(s: Skill) -> SkillOut:
    return SkillOut(**_summary_fields(s), body=s.body)


def _skill_summary_out(s: Skill) -> SkillSummaryOut:
    return SkillSummaryOut(**_summary_fields(s))


# ---------------------------------------------------------------------------
# Scope-parametrised handlers — one implementation, four call sites
# ---------------------------------------------------------------------------


async def _list(
    db: AsyncSession,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    pagination: PaginationParams,
    *,
    include_deleted: bool,
) -> SkillPageOut:
    rows, total = await SkillsFacade(db).list_for_scope(
        scope=scope,
        owner_id=owner_id,
        limit=pagination.limit,
        offset=pagination.offset,
        include_deleted=include_deleted,
    )
    return SkillPageOut(items=[_skill_summary_out(s) for s in rows], total=total)


async def _create(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    body: SkillCreateIn,
) -> SkillOut:
    skill = await SkillsFacade(db).create(
        scope=scope,
        owner_id=owner_id,
        name=body.name,
        description=body.description,
        body=body.body,
        requires=tuple(body.requires),
        allowed_tools=tuple(body.allowed_tools),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _skill_out(skill)


async def _get(
    db: AsyncSession, scope: SkillScope, owner_id: uuid.UUID | None, skill_id: uuid.UUID
) -> SkillOut:
    return _skill_out(await SkillsFacade(db).get_owned(skill_id, scope, owner_id=owner_id))


async def _patch(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    body: SkillPatchIn,
    if_match: str | None,
) -> SkillOut:
    fields = body.model_dump(exclude_unset=True)
    draft = SkillDraft(
        description=fields.get("description"),
        body=fields.get("body"),
        requires=tuple(fields["requires"]) if fields.get("requires") is not None else None,
        allowed_tools=tuple(fields["allowed_tools"]) if fields.get("allowed_tools") is not None else None,
    )
    skill = await SkillsFacade(db).update(
        skill_id,
        scope,
        owner_id=owner_id,
        draft=draft,
        expected_version=parse_if_match(if_match),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _skill_out(skill)


async def _delete(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    if_match: str | None,
) -> None:
    await SkillsFacade(db).soft_delete(
        skill_id,
        scope,
        owner_id=owner_id,
        expected_version=parse_if_match(if_match),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


async def _restore(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
) -> SkillOut:
    skill = await SkillsFacade(db).restore(
        skill_id,
        scope,
        owner_id=owner_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _skill_out(skill)


async def _list_files(
    db: AsyncSession, scope: SkillScope, owner_id: uuid.UUID | None, skill_id: uuid.UUID
) -> list[SkillFileOut]:
    rows = await SkillsFacade(db).list_files(skill_id, scope, owner_id=owner_id)
    return [_file_out(f) for f in rows]


async def _create_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    body: SkillFileCreateIn,
) -> SkillFileOut:
    created = await SkillsFacade(db).add_file(
        skill_id,
        scope,
        owner_id=owner_id,
        path=body.path,
        data=body.content.encode("utf-8"),
        mime=body.mime,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _file_out(created)


async def _upload_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    path: str,
    upload: UploadFile,
) -> SkillFileOut:
    """The uploaded-bytes twin of `_create_file` (AC-16, Q-20).

    Multipart only, with no tus path, and that is a consequence rather than an omission:
    Q-17 caps a skill file at 32 MB, which is exactly `MAX_SKILL_FILE_BYTES` — the same
    number as the RAG multipart cap — so a single file can never exceed what one request
    carries. Bundles are what need tus, and they are Phase 4.
    """
    # Raised as an HTTPException rather than letting `_validate_file_path`'s ValueError
    # escape: `path` arrives as a `Form` field, so unlike `SkillFileCreateIn.path` there
    # is no Pydantic validator to turn a ValueError into a 422, and nothing registers a
    # ValueError handler — a bad path would answer 500 with a stack trace instead of
    # naming the problem. Fail-closed either way; only the status and the log were wrong.
    reason = skill_file_path_reason(path)
    if reason is not None:
        raise HTTPException(status_code=422, detail=f"path {reason}")
    # Read the cap plus one byte, so an oversized upload is rejected without this process
    # ever holding it — `rag.upload_document` does the same.
    #
    # What this does **not** do, despite how it reads: bound the transfer. Starlette has
    # already streamed the whole multipart body into a SpooledTemporaryFile before this
    # handler is called, so a 10 GB upload is fully received and spilled to disk, and only
    # then refused. The bound on *that* is nginx's `client_max_body_size`, not this line.
    # What this line buys is that the oversize case never becomes a 32 MB+ `bytes` in the
    # worker's heap.
    data = await upload.read(MAX_SKILL_FILE_BYTES + 1)
    if len(data) > MAX_SKILL_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds {MAX_SKILL_FILE_BYTES} bytes",
        )
    created = await SkillsFacade(db).add_file(
        skill_id,
        scope,
        owner_id=owner_id,
        path=path,
        data=data,
        # The client's declared type is a hint; `normalise_mime` prefers the path's own
        # extension when it is absent or the generic octet-stream.
        mime=upload.content_type or "",
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _file_out(created)


async def _patch_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    file_id: uuid.UUID,
    body: SkillFilePatchIn,
) -> SkillFileOut:
    updated = await SkillsFacade(db).update_file(
        skill_id,
        scope,
        file_id,
        owner_id=owner_id,
        data=body.content.encode("utf-8"),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _file_out(updated)


async def _delete_file(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    await SkillsFacade(db).delete_file(
        skill_id,
        scope,
        file_id,
        owner_id=owner_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


async def _copy(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    body: SkillCopyIn,
) -> SkillOut:
    await _assert_may_write_scope(db, principal, body.target_scope, body.target_owner_id)
    skill = await SkillsFacade(db).copy(
        skill_id,
        scope,
        owner_id=owner_id,
        target_scope=body.target_scope,
        target_owner_id=body.target_owner_id,
        name=body.name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _skill_out(skill)


async def _assert_may_write_scope(
    db: AsyncSession,
    principal: Principal,
    scope: SkillScope,
    owner_id: uuid.UUID | None,
) -> None:
    """Authorize the *target* of a copy, which no router-level guard can cover.

    Copy is the one endpoint whose destination is in the body rather than the path (it has
    to be — the whole point is to cross scopes), so the source router's guard says nothing
    about where the copy lands. Without this, any project member could copy a skill into
    the platform scope and have it offered to every agent on the deployment.
    """
    from contexts.agents.interfaces.facade import AgentsFacade
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    if scope is SkillScope.PLATFORM:
        if not principal.is_admin:
            _raise_forbidden("only an admin may write platform-scoped skills")
        return

    if owner_id is None:
        raise HTTPException(status_code=422, detail=f"target_owner_id is required for {scope.value} scope")

    if principal.is_admin:
        return

    if scope is SkillScope.ORG:
        capability, check_scope = Capability.SKILL_ORG_MANAGE, Scope(org_id=owner_id)
    elif scope is SkillScope.PROJECT:
        capability, check_scope = Capability.RESOURCE_CREATE_EDIT, Scope(project_id=owner_id)
    else:
        agent = await AgentsFacade(db).get_agent(owner_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="target agent not found")
        capability, check_scope = Capability.RESOURCE_CREATE_EDIT, Scope(project_id=agent.project_id)

    resolver = await get_role_resolver(db)
    decision = await decide(principal, capability, check_scope, resolver)
    if not decision.allowed:
        _raise_forbidden(decision.reason)


async def _agent_project_id(db: AsyncSession, agent_id: uuid.UUID) -> uuid.UUID:
    from contexts.agents.interfaces.facade import AgentsFacade

    agent = await AgentsFacade(db).get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent.project_id


async def _assert_agent_write(db: AsyncSession, principal: Principal, agent_id: uuid.UUID) -> None:
    """RESOURCE_CREATE_EDIT at the agent's own project.

    The agent routers cannot use `scope_from_path` — there is no `{project_id}` in the
    path — so the project is lifted off the agent first, mirroring `agents.py:265-275`.
    """
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    project_id = await _agent_project_id(db, agent_id)
    if principal.is_admin:
        return
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal, Capability.RESOURCE_CREATE_EDIT, Scope(project_id=project_id), resolver
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


# ---------------------------------------------------------------------------
# /api/agents/{agent_id}/skills — agent scope
# ---------------------------------------------------------------------------


@agent_router.get("")
async def agent_list_skills(
    agent_id: uuid.UUID = Path(...),
    include_deleted: bool = False,
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillPageOut:
    await assert_project_membership(
        db=db, principal=principal, project_id=await _agent_project_id(db, agent_id)
    )
    return await _list(db, SkillScope.AGENT, agent_id, pagination, include_deleted=include_deleted)


@agent_router.post("", status_code=status.HTTP_201_CREATED)
async def agent_create_skill(
    body: SkillCreateIn,
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _create(db, ctx, principal, SkillScope.AGENT, agent_id, body)


@agent_router.get("/{skill_id}")
async def agent_get_skill(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await assert_project_membership(
        db=db, principal=principal, project_id=await _agent_project_id(db, agent_id)
    )
    return await _get(db, SkillScope.AGENT, agent_id, skill_id)


@agent_router.patch("/{skill_id}")
async def agent_patch_skill(
    body: SkillPatchIn,
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _patch(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, body, if_match)


@agent_router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def agent_delete_skill(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _assert_agent_write(db, principal, agent_id)
    await _delete(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, if_match)


@agent_router.post("/{skill_id}/restore")
async def agent_restore_skill(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _restore(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id)


@agent_router.post("/{skill_id}/copy", status_code=status.HTTP_201_CREATED)
async def agent_copy_skill(
    body: SkillCopyIn,
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _copy(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, body)


@agent_router.get("/{skill_id}/files")
async def agent_list_files(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SkillFileOut]:
    await assert_project_membership(
        db=db, principal=principal, project_id=await _agent_project_id(db, agent_id)
    )
    return await _list_files(db, SkillScope.AGENT, agent_id, skill_id)


@agent_router.post("/{skill_id}/files", status_code=status.HTTP_201_CREATED)
async def agent_create_file(
    body: SkillFileCreateIn,
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _create_file(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, body)


@agent_router.post("/{skill_id}/files/upload", status_code=status.HTTP_201_CREATED)
async def agent_upload_file(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    path: str = Form(...),
    upload: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _upload_file(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, path, upload)


@agent_router.patch("/{skill_id}/files/{file_id}")
async def agent_patch_file(
    body: SkillFilePatchIn,
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    await _assert_agent_write(db, principal, agent_id)
    return await _patch_file(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, file_id, body)


@agent_router.delete(
    "/{skill_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def agent_delete_file(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _assert_agent_write(db, principal, agent_id)
    await _delete_file(db, ctx, principal, SkillScope.AGENT, agent_id, skill_id, file_id)


# ---------------------------------------------------------------------------
# /api/projects/{project_id}/skills — project scope
# ---------------------------------------------------------------------------

_PROJECT_GUARD = Depends(
    require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(project_param="project_id"))
)


@project_router.get("")
async def project_list_skills(
    project_id: uuid.UUID = Path(...),
    include_deleted: bool = False,
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillPageOut:
    # Read is membership, not RESOURCE_CREATE_EDIT: a project member must be able to see
    # what instructions their agents can be given.
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    return await _list(db, SkillScope.PROJECT, project_id, pagination, include_deleted=include_deleted)


@project_router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_PROJECT_GUARD])
async def project_create_skill(
    body: SkillCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _create(db, ctx, principal, SkillScope.PROJECT, project_id, body)


@project_router.get("/{skill_id}")
async def project_get_skill(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    return await _get(db, SkillScope.PROJECT, project_id, skill_id)


@project_router.patch("/{skill_id}", dependencies=[_PROJECT_GUARD])
async def project_patch_skill(
    body: SkillPatchIn,
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _patch(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, body, if_match)


@project_router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[_PROJECT_GUARD],
)
async def project_delete_skill(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, if_match)


@project_router.post("/{skill_id}/restore", dependencies=[_PROJECT_GUARD])
async def project_restore_skill(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _restore(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id)


@project_router.post("/{skill_id}/copy", status_code=status.HTTP_201_CREATED, dependencies=[_PROJECT_GUARD])
async def project_copy_skill(
    body: SkillCopyIn,
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _copy(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, body)


@project_router.get("/{skill_id}/files")
async def project_list_files(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SkillFileOut]:
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    return await _list_files(db, SkillScope.PROJECT, project_id, skill_id)


@project_router.post("/{skill_id}/files", status_code=status.HTTP_201_CREATED, dependencies=[_PROJECT_GUARD])
async def project_create_file(
    body: SkillFileCreateIn,
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _create_file(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, body)


@project_router.post(
    "/{skill_id}/files/upload", status_code=status.HTTP_201_CREATED, dependencies=[_PROJECT_GUARD]
)
async def project_upload_file(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    path: str = Form(...),
    upload: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _upload_file(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, path, upload)


@project_router.patch("/{skill_id}/files/{file_id}", dependencies=[_PROJECT_GUARD])
async def project_patch_file(
    body: SkillFilePatchIn,
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _patch_file(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, file_id, body)


@project_router.delete(
    "/{skill_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[_PROJECT_GUARD],
)
async def project_delete_file(
    project_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_file(db, ctx, principal, SkillScope.PROJECT, project_id, skill_id, file_id)


# ---------------------------------------------------------------------------
# /api/orgs/{org_id}/skills — org scope (Org Owner)
# ---------------------------------------------------------------------------

_ORG_GUARD = Depends(require(Capability.SKILL_ORG_MANAGE, scope_from_path(org_param="org_id")))


@org_router.get("", dependencies=[_ORG_GUARD])
async def org_list_skills(
    org_id: uuid.UUID = Path(...),
    include_deleted: bool = False,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
) -> SkillPageOut:
    return await _list(db, SkillScope.ORG, org_id, pagination, include_deleted=include_deleted)


@org_router.post("", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD])
async def org_create_skill(
    body: SkillCreateIn,
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _create(db, ctx, principal, SkillScope.ORG, org_id, body)


@org_router.get("/{skill_id}", dependencies=[_ORG_GUARD])
async def org_get_skill(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _get(db, SkillScope.ORG, org_id, skill_id)


@org_router.patch("/{skill_id}", dependencies=[_ORG_GUARD])
async def org_patch_skill(
    body: SkillPatchIn,
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _patch(db, ctx, principal, SkillScope.ORG, org_id, skill_id, body, if_match)


@org_router.delete(
    "/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, dependencies=[_ORG_GUARD]
)
async def org_delete_skill(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete(db, ctx, principal, SkillScope.ORG, org_id, skill_id, if_match)


@org_router.post("/{skill_id}/restore", dependencies=[_ORG_GUARD])
async def org_restore_skill(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _restore(db, ctx, principal, SkillScope.ORG, org_id, skill_id)


@org_router.post("/{skill_id}/copy", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD])
async def org_copy_skill(
    body: SkillCopyIn,
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _copy(db, ctx, principal, SkillScope.ORG, org_id, skill_id, body)


@org_router.get("/{skill_id}/files", dependencies=[_ORG_GUARD])
async def org_list_files(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> list[SkillFileOut]:
    return await _list_files(db, SkillScope.ORG, org_id, skill_id)


@org_router.post("/{skill_id}/files", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD])
async def org_create_file(
    body: SkillFileCreateIn,
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _create_file(db, ctx, principal, SkillScope.ORG, org_id, skill_id, body)


@org_router.post("/{skill_id}/files/upload", status_code=status.HTTP_201_CREATED, dependencies=[_ORG_GUARD])
async def org_upload_file(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    path: str = Form(...),
    upload: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _upload_file(db, ctx, principal, SkillScope.ORG, org_id, skill_id, path, upload)


@org_router.patch("/{skill_id}/files/{file_id}", dependencies=[_ORG_GUARD])
async def org_patch_file(
    body: SkillFilePatchIn,
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _patch_file(db, ctx, principal, SkillScope.ORG, org_id, skill_id, file_id, body)


@org_router.delete(
    "/{skill_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[_ORG_GUARD],
)
async def org_delete_file(
    org_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_file(db, ctx, principal, SkillScope.ORG, org_id, skill_id, file_id)


# ---------------------------------------------------------------------------
# /api/admin/skills — platform scope (Admin)
# ---------------------------------------------------------------------------


@admin_router.get("", dependencies=[Depends(require_admin)])
async def admin_list_skills(
    include_deleted: bool = False,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
) -> SkillPageOut:
    return await _list(db, SkillScope.PLATFORM, None, pagination, include_deleted=include_deleted)


@admin_router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def admin_create_skill(
    body: SkillCreateIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _create(db, ctx, principal, SkillScope.PLATFORM, None, body)


@admin_router.get("/metrics", dependencies=[Depends(require_admin)])
async def admin_skill_metrics(db: AsyncSession = Depends(db_session)) -> SkillScopeCountsOut:
    """[R31.11] / AC-15 — the ratio of agent-private to shared skills.

    Not decoration: §5's premise is that skills are shared at project scope and above.
    If most end up agent-scoped, Skills has degraded into the §9.2 prompt-strategy
    feature it replaced, and this endpoint is what makes that visible rather than a
    matter of opinion at the six-month review.

    Declared **before** `/{skill_id}`: FastAPI matches in declaration order, and the
    UUID-typed path below would otherwise claim "metrics" and 422 it.
    """
    counts = await SkillsFacade(db).count_by_scope()
    return SkillScopeCountsOut(
        # Every scope is named even at zero. An absent key reads as "unknown", and a
        # ratio with a missing denominator is the thing this endpoint exists to give.
        counts={scope.value: counts.counts.get(scope, 0) for scope in SkillScope},
        total=counts.total,
    )


@admin_router.get("/{skill_id}", dependencies=[Depends(require_admin)])
async def admin_get_skill(
    skill_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _get(db, SkillScope.PLATFORM, None, skill_id)


@admin_router.patch("/{skill_id}", dependencies=[Depends(require_admin)])
async def admin_patch_skill(
    body: SkillPatchIn,
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _patch(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, body, if_match)


@admin_router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_admin)],
)
async def admin_delete_skill(
    skill_id: uuid.UUID = Path(...),
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, if_match)


@admin_router.post("/{skill_id}/restore", dependencies=[Depends(require_admin)])
async def admin_restore_skill(
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _restore(db, ctx, principal, SkillScope.PLATFORM, None, skill_id)


@admin_router.post(
    "/{skill_id}/copy", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)]
)
async def admin_copy_skill(
    body: SkillCopyIn,
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillOut:
    return await _copy(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, body)


@admin_router.get("/{skill_id}/files", dependencies=[Depends(require_admin)])
async def admin_list_files(
    skill_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> list[SkillFileOut]:
    return await _list_files(db, SkillScope.PLATFORM, None, skill_id)


@admin_router.post(
    "/{skill_id}/files", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)]
)
async def admin_create_file(
    body: SkillFileCreateIn,
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _create_file(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, body)


@admin_router.post(
    "/{skill_id}/files/upload",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def admin_upload_file(
    skill_id: uuid.UUID = Path(...),
    path: str = Form(...),
    upload: UploadFile = File(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _upload_file(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, path, upload)


@admin_router.patch("/{skill_id}/files/{file_id}", dependencies=[Depends(require_admin)])
async def admin_patch_file(
    body: SkillFilePatchIn,
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> SkillFileOut:
    return await _patch_file(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, file_id, body)


@admin_router.delete(
    "/{skill_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_admin)],
)
async def admin_delete_file(
    skill_id: uuid.UUID = Path(...),
    file_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _delete_file(db, ctx, principal, SkillScope.PLATFORM, None, skill_id, file_id)


# ---------------------------------------------------------------------------
# /api/agents/{agent_id}/skill-bindings — bind / unbind
# ---------------------------------------------------------------------------


@binding_router.get("")
async def list_bindings(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SkillBindingOut]:
    project_id = await _agent_project_id(db, agent_id)
    await assert_project_membership(db=db, principal=principal, project_id=project_id)
    # This endpoint answers "what would actually be live on the next turn", so it runs the
    # same tap the turn runs and must feed it the same input. A turn shares one tool
    # snapshot across its consumers; a request has none to share, so it reads its own.
    from contexts.agents.interfaces.facade import AgentsFacade

    agent_tools = await AgentsFacade(db).list_agent_tools(agent_id)
    bound = await SkillsFacade(db).resolve_bound_set(
        agent_id=agent_id,
        agent_project_id=project_id,
        enabled_tools={t.tool_type for t in agent_tools if t.enabled},
    )
    return [SkillBindingOut(agent_id=agent_id, skill_id=s.id, name=s.name) for s in bound.skills]


@binding_router.put("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def bind_skill(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Bind a skill to an agent.

    The path carries no scope, so the capability check below authorizes the *agent* only.
    `resolve_bindable` inside `bind` is what proves the skill's scope contains it — taking
    `skill_id` on trust here is the SEC-H1 IDOR with instructions in place of chunks.
    """
    await _assert_agent_write(db, principal, agent_id)
    skill = await SkillsFacade(db).bind(skill_id=skill_id, agent_id=agent_id)
    await _emit_binding_audit(db, ctx, principal, "skill.bound", agent_id, skill)


@binding_router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unbind_skill(
    agent_id: uuid.UUID = Path(...),
    skill_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _assert_agent_write(db, principal, agent_id)
    skill = await SkillsFacade(db).unbind(skill_id=skill_id, agent_id=agent_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill is not bound to this agent")
    await _emit_binding_audit(db, ctx, principal, "skill.unbound", agent_id, skill)


async def _emit_binding_audit(
    db: AsyncSession,
    ctx: RequestContext,
    principal: Principal,
    action: str,
    agent_id: uuid.UUID,
    skill: Skill,
) -> None:
    from shared_kernel import audit

    await audit.emit(
        db,
        audit.AuditEvent(
            action=action,
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="skill",
            resource_id=skill.id,
            metadata={
                "agent_id": str(agent_id),
                "scope": skill.scope.value,
                "name": skill.name,
                # The bound body is what will execute; recording its hash is what makes a
                # later "which bytes did this agent have?" answerable.
                "body_sha256": skill.body_sha256,
            },
            request_id=ctx.request_id,
        ),
    )


__all__ = [
    "admin_router",
    "agent_router",
    "binding_router",
    "org_router",
    "project_router",
]
