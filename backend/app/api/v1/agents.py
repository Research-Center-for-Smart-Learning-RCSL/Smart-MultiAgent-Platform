"""`/api/projects/{pid}/agents` + `/api/agents/{id}` — agent CRUD (§22.6).

Splits across two routers because the collection is project-scoped
(`/api/projects/{pid}/agents`) while the item is id-addressable
(`/api/agents/{id}`) — same split the tenancy and keys contexts use.

AuthZ (§5.2, cap #15 `RESOURCE_CREATE_EDIT`):
- List: requires project membership (read-only).
- Create / Patch / Delete: requires `RESOURCE_CREATE_EDIT` at the project scope.
- MCP list/attach/detach: same capability.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.models import (
    AgentDraft,
    AgentModelHint,
    ContextMode,
    PromptStrategy,
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
from shared_kernel.validation import BoundedConfig

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


# API-7: upper bounds on free-text / list fields — an unbounded system prompt
# or tool list lets a single request drive memory / DB load.
_MAX_SYSTEM_PROMPT = 100_000
_MAX_REFERENCE = 2_000
_MAX_ALLOWED_TOOLS = 200


class AgentCreateIn(BaseModel):
    model_config = {"protected_namespaces": ()}

    name: str = Field(min_length=1, max_length=200)
    model_hint: Literal["claude", "openai", "gemini"]
    model_id: str | None = Field(default=None, max_length=200)
    key_group_id: uuid.UUID

    @field_validator("model_id", mode="before")
    @classmethod
    def _strip_model_id(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    system_prompt: str = Field(default="", max_length=_MAX_SYSTEM_PROMPT)
    prompt_strategy: Literal["full", "lazy"] = "full"
    rag_config_id: uuid.UUID | None = None
    graphrag_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] = "general"
    context_token_cap: int | None = Field(default=None, gt=0)
    a2a_enabled: bool = False
    wakeup_config: BoundedConfig = Field(default_factory=dict)
    workflow_capabilities: BoundedConfig = Field(default_factory=dict)


class AgentPatchIn(BaseModel):
    # Every field is optional; missing → no change.
    # For nullable fields (`rag_config_id`, `graphrag_config_id`,
    # `context_token_cap`) send `null` explicitly to clear.
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_hint: Literal["claude", "openai", "gemini"] | None = None
    model_id: str | None = Field(default=None, max_length=200)
    key_group_id: uuid.UUID | None = None

    @field_validator("model_id", mode="before")
    @classmethod
    def _strip_model_id(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    system_prompt: str | None = Field(default=None, max_length=_MAX_SYSTEM_PROMPT)
    prompt_strategy: Literal["full", "lazy"] | None = None
    rag_config_id: uuid.UUID | None = None
    graphrag_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] | None = None
    context_token_cap: int | None = Field(default=None, gt=0)
    a2a_enabled: bool | None = None
    wakeup_config: BoundedConfig | None = None
    workflow_capabilities: BoundedConfig | None = None


class AgentOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    model_hint: str
    model_id: str | None
    key_group_id: uuid.UUID
    system_prompt: str
    prompt_strategy: str
    rag_config_id: uuid.UUID | None
    graphrag_config_id: uuid.UUID | None
    context_mode: str
    context_token_cap: int | None
    a2a_enabled: bool
    wakeup_config: dict[str, Any]
    workflow_capabilities: dict[str, Any]
    version: int
    created_at: str
    deleted_at: str | None


def _to_agent_out(a) -> AgentOut:
    return AgentOut(
        id=a.id,
        project_id=a.project_id,
        name=a.name,
        model_hint=a.model_hint.value,
        model_id=a.model_id,
        key_group_id=a.key_group_id,
        system_prompt=a.system_prompt,
        prompt_strategy=a.prompt_strategy.value,
        rag_config_id=a.rag_config_id,
        graphrag_config_id=a.graphrag_config_id,
        context_mode=a.context_mode.value,
        context_token_cap=a.context_token_cap,
        a2a_enabled=a.a2a_enabled,
        wakeup_config=a.wakeup_config,
        workflow_capabilities=a.workflow_capabilities,
        version=a.version,
        created_at=a.created_at.isoformat(),
        deleted_at=a.deleted_at.isoformat() if a.deleted_at else None,
    )


def _parse_if_match(raw: str) -> int:
    try:
        return int(raw.strip().strip('"'))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=412,
            detail=f"invalid If-Match: {raw!r}",
        ) from exc


# ---------------------------------------------------------------------------
# Project-scoped collection routes
# ---------------------------------------------------------------------------

project_router = APIRouter(prefix="/api/projects/{project_id}/agents", tags=["agents"])


@project_router.get("")
async def list_project_agents(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[AgentOut]:
    service = AgentService(db)
    rows = await service.list_for_project(
        project_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return [_to_agent_out(r) for r in rows]


@project_router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(
        require(
            Capability.RESOURCE_CREATE_EDIT,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> AgentOut:
    service = AgentService(db)
    draft = AgentDraft(
        name=body.name,
        model_hint=AgentModelHint(body.model_hint),
        model_id=body.model_id,
        key_group_id=body.key_group_id,
        system_prompt=body.system_prompt,
        prompt_strategy=PromptStrategy(body.prompt_strategy),
        rag_config_id=body.rag_config_id,
        graphrag_config_id=body.graphrag_config_id,
        context_mode=ContextMode(body.context_mode),
        context_token_cap=body.context_token_cap,
        a2a_enabled=body.a2a_enabled,
        wakeup_config=body.wakeup_config,
        workflow_capabilities=body.workflow_capabilities,
    )
    agent = await service.create(
        project_id=project_id,
        draft=draft,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_agent_out(agent)


# ---------------------------------------------------------------------------
# Agent-id-addressable routes
# ---------------------------------------------------------------------------


agent_router = APIRouter(prefix="/api/agents", tags=["agents"])


async def _agent_scope_dep(
    agent_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
):
    """Resolve the agent's project so `require(...)` can scope the check.

    Lifts the `project_id` off the agent so call sites don't need to embed
    `{project_id}` in the path.
    """
    service = AgentService(db)
    return await service.get(agent_id)


@agent_router.get("/{agent_id}")
async def read_agent(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentOut:
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    service = AgentService(db)
    agent = await service.get(agent_id)
    # Membership check against the agent's project.
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=agent.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the agent's project")
    return _to_agent_out(agent)


@agent_router.patch("/{agent_id}")
async def patch_agent(
    body: AgentPatchIn,
    agent_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentOut:
    expected = _parse_if_match(if_match)
    service = AgentService(db)
    agent = await service.get(agent_id)

    # AuthZ at the agent's project scope.
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    fields = body.model_dump(exclude_unset=True)
    draft = AgentDraft(
        name=fields.get("name"),
        model_hint=AgentModelHint(fields["model_hint"]) if "model_hint" in fields else None,
        model_id=fields.get("model_id"),
        key_group_id=fields.get("key_group_id"),
        system_prompt=fields.get("system_prompt"),
        prompt_strategy=(PromptStrategy(fields["prompt_strategy"]) if "prompt_strategy" in fields else None),
        rag_config_id=fields.get("rag_config_id"),
        graphrag_config_id=fields.get("graphrag_config_id"),
        context_mode=(ContextMode(fields["context_mode"]) if "context_mode" in fields else None),
        context_token_cap=fields.get("context_token_cap"),
        a2a_enabled=fields.get("a2a_enabled"),
        wakeup_config=fields.get("wakeup_config"),
        workflow_capabilities=fields.get("workflow_capabilities"),
        # Distinguish "explicit null" from "omitted".
        clear_model_id="model_id" in fields and fields["model_id"] is None,
        clear_rag_config="rag_config_id" in fields and fields["rag_config_id"] is None,
        clear_graphrag_config=("graphrag_config_id" in fields and fields["graphrag_config_id"] is None),
        clear_context_token_cap=("context_token_cap" in fields and fields["context_token_cap"] is None),
    )
    updated = await service.patch(
        agent_id=agent_id,
        draft=draft,
        expected_version=expected,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_agent_out(updated)


@agent_router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_agent(
    agent_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    expected = _parse_if_match(if_match)
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    await service.soft_delete(
        agent_id=agent_id,
        expected_version=expected,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# ---------------------------------------------------------------------------
# Unified Agent Tools surface (Phase A — /api/agents/{id}/tools)
# ---------------------------------------------------------------------------

_MAX_DISPLAY_NAME = 200


class AgentToolOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    tool_type: str
    enabled: bool
    display_name: str | None
    config: dict[str, Any]
    config_warnings: list[str] = []
    created_at: str


_MAX_CONFIG_KEYS = 20
_MAX_AUTH_KEYS = 10


class AgentToolCreateIn(BaseModel):
    model_config = {"extra": "forbid"}
    tool_type: Literal["hosted_mcp", "local_function"]
    display_name: str | None = Field(default=None, max_length=_MAX_DISPLAY_NAME)
    config: dict[str, Any] = Field(default_factory=dict)
    auth: dict[str, Any] | None = None

    @field_validator("config")
    @classmethod
    def _cap_config(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > _MAX_CONFIG_KEYS:
            raise ValueError(f"config must have at most {_MAX_CONFIG_KEYS} keys")
        return v

    @field_validator("auth")
    @classmethod
    def _cap_auth(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(v) > _MAX_AUTH_KEYS:
            raise ValueError(f"auth must have at most {_MAX_AUTH_KEYS} keys")
        return v


class AgentToolPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=_MAX_DISPLAY_NAME)
    config: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    # Explicitly remove a stored credential (auth=None is "unchanged", not "clear").
    clear_auth: bool = False

    @field_validator("config")
    @classmethod
    def _cap_config(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(v) > _MAX_CONFIG_KEYS:
            raise ValueError(f"config must have at most {_MAX_CONFIG_KEYS} keys")
        return v

    @field_validator("auth")
    @classmethod
    def _cap_auth(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(v) > _MAX_AUTH_KEYS:
            raise ValueError(f"auth must have at most {_MAX_AUTH_KEYS} keys")
        return v


def _to_tool_out(t, *, config_warnings: list[str] | None = None) -> AgentToolOut:
    cfg = {k: v for k, v in t.config.items() if k not in ("auth", "auth_present")}
    if "auth" in t.config:
        cfg["auth_present"] = True
    return AgentToolOut(
        id=t.id,
        agent_id=t.agent_id,
        tool_type=t.tool_type.value if hasattr(t.tool_type, "value") else str(t.tool_type),
        enabled=t.enabled,
        display_name=t.display_name,
        config=cfg,
        config_warnings=config_warnings or [],
        created_at=t.created_at.isoformat(),
    )


async def _function_warnings(db: AsyncSession, project_id: uuid.UUID, tool: Any) -> list[str]:
    from contexts.agents.domain.models import AgentToolType

    if not hasattr(tool, "tool_type") or tool.tool_type != AgentToolType.LOCAL_FUNCTION:
        return []
    http = (tool.config or {}).get("http", {})
    url = http.get("url", "")
    if not url:
        return []
    from urllib.parse import urlsplit

    from contexts.agents.infrastructure.mcp_repositories import EgressAllowlistRepository

    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return []
    repo = EgressAllowlistRepository(db)
    if not await repo.is_allowed(project_id=project_id, hostname=host):
        return [f"host {host} is not on the project egress allowlist"]
    return []


@agent_router.get("/{agent_id}/tools")
async def list_agent_tools(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(db_session),
) -> list[AgentToolOut]:
    service = AgentService(db)
    agent = await service.get(agent_id)
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=agent.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the agent's project")
    tools = await service.list_tools(
        agent_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    results: list[AgentToolOut] = []
    for t in tools:
        warnings = await _function_warnings(db, agent.project_id, t)
        results.append(_to_tool_out(t, config_warnings=warnings))
    return results


@agent_router.post("/{agent_id}/tools", status_code=status.HTTP_201_CREATED)
async def add_agent_tool(
    body: AgentToolCreateIn,
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentToolOut:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from contexts.agents.domain.models import AgentToolType
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    try:
        tool = await service.add_tool(
            agent_id=agent_id,
            tool_type=AgentToolType(body.tool_type),
            display_name=body.display_name,
            config=body.config,
            auth=body.auth,
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    warnings = await _function_warnings(db, agent.project_id, tool)
    return _to_tool_out(tool, config_warnings=warnings)


@agent_router.patch("/{agent_id}/tools/{tool_id}")
async def patch_agent_tool(
    body: AgentToolPatchIn,
    agent_id: uuid.UUID = Path(...),
    tool_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AgentToolOut:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    fields = body.model_dump(exclude_unset=True)
    try:
        tool = await service.patch_tool(
            agent_id=agent_id,
            tool_id=tool_id,
            enabled=fields.get("enabled"),
            display_name=fields.get("display_name"),
            clear_display_name="display_name" in fields and fields["display_name"] is None,
            config=fields.get("config"),
            auth=fields.get("auth"),
            clear_auth=fields.get("clear_auth", False),
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    warnings = await _function_warnings(db, agent.project_id, tool)
    return _to_tool_out(tool, config_warnings=warnings)


@agent_router.delete(
    "/{agent_id}/tools/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_agent_tool(
    agent_id: uuid.UUID = Path(...),
    tool_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    await service.remove_tool(
        agent_id=agent_id,
        tool_id=tool_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


def _get_sandbox_runner():  # pragma: no cover - runtime injection
    from contexts.agents.infrastructure.sandbox.docker_runsc import (
        docker_runsc_sandbox_from_settings,
    )

    return docker_runsc_sandbox_from_settings()


class AgentToolTestOut(BaseModel):
    ok: bool
    tool_names: list[str]
    duration_ms: int
    error: str | None = None
    # HTTP status from a local_function reachability probe (None for MCP tests).
    status: int | None = None


@agent_router.post("/{agent_id}/tools/{tool_id}/test")
async def test_agent_tool(
    agent_id: uuid.UUID = Path(...),
    tool_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
    runner=Depends(_get_sandbox_runner),
) -> AgentToolTestOut:
    service = AgentService(db)
    agent = await service.get(agent_id)

    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope, decide

    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=agent.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    from contexts.agents.domain.models import AgentToolType

    tools = await service.list_tools(agent_id)
    tool = next((t for t in tools if t.id == tool_id), None)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if tool.tool_type not in (AgentToolType.HOSTED_MCP, AgentToolType.LOCAL_FUNCTION):
        raise HTTPException(
            status_code=422, detail="only hosted_mcp and local_function tools can be tested"
        )

    result = await service.probe_tool(
        agent=agent,
        tool=tool,
        runner=runner,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return AgentToolTestOut(
        ok=result.ok,
        tool_names=list(result.tool_names),
        status=result.status,
        duration_ms=result.duration_ms,
        error=result.error,
    )


__all__ = ["agent_router", "project_router"]
