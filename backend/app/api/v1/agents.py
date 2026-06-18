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

from app.api.v1.deps import PaginationParams
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.models import (
    AgentDraft,
    AgentModelHint,
    ContextMode,
    McpSource,
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
    system_prompt: str = Field(default="", max_length=_MAX_SYSTEM_PROMPT)
    prompt_strategy: Literal["full", "lazy"] = "full"
    rag_config_id: uuid.UUID | None = None
    graphrag_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] = "general"
    context_token_cap: int | None = Field(default=None, gt=0)
    a2a_enabled: bool = False
    wakeup_config: dict[str, Any] = Field(default_factory=dict)
    workflow_capabilities: dict[str, Any] = Field(default_factory=dict)


class AgentPatchIn(BaseModel):
    # Every field is optional; missing → no change.
    # For nullable fields (`rag_config_id`, `graphrag_config_id`,
    # `context_token_cap`) send `null` explicitly to clear.
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_hint: Literal["claude", "openai", "gemini"] | None = None
    model_id: str | None = Field(default=None, max_length=200)
    key_group_id: uuid.UUID | None = None
    system_prompt: str | None = Field(default=None, max_length=_MAX_SYSTEM_PROMPT)
    prompt_strategy: Literal["full", "lazy"] | None = None
    rag_config_id: uuid.UUID | None = None
    graphrag_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] | None = None
    context_token_cap: int | None = Field(default=None, gt=0)
    a2a_enabled: bool | None = None
    wakeup_config: dict[str, Any] | None = None
    workflow_capabilities: dict[str, Any] | None = None


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


class McpBindingCreateIn(BaseModel):
    source: Literal["builtin", "url", "package"]
    reference: str = Field(min_length=1, max_length=_MAX_REFERENCE)
    allowed_tools: list[str] = Field(default_factory=list, max_length=_MAX_ALLOWED_TOOLS)
    config: dict[str, Any] = Field(default_factory=dict)


class McpBindingPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    allowed_tools: list[str] | None = Field(default=None, max_length=_MAX_ALLOWED_TOOLS)
    config: dict[str, Any] | None = None


class McpBindingOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    source: str
    reference: str
    allowed_tools: list[str]
    config: dict[str, Any]
    created_at: str


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


def _to_binding_out(b) -> McpBindingOut:
    return McpBindingOut(
        id=b.id,
        agent_id=b.agent_id,
        source=b.source.value,
        reference=b.reference,
        allowed_tools=list(b.allowed_tools),
        config=b.config,
        created_at=b.created_at.isoformat(),
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
        project_id, limit=pagination.limit, offset=pagination.offset,
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
# MCP bindings (subset; full surface in E.9)
# ---------------------------------------------------------------------------


@agent_router.get("/{agent_id}/mcp")
async def list_mcp_bindings(
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[McpBindingOut]:
    service = AgentService(db)
    agent = await service.get(agent_id)
    from shared_kernel.auth.dependencies import _raise_forbidden, get_role_resolver
    from shared_kernel.auth.permissions import Scope

    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(principal, Scope(project_id=agent.project_id))
        if not roles:
            _raise_forbidden("caller is not a member of the agent's project")
    return [_to_binding_out(b) for b in await service.list_mcp_bindings(agent_id)]


@agent_router.post("/{agent_id}/mcp", status_code=status.HTTP_201_CREATED)
async def add_mcp_binding(
    body: McpBindingCreateIn,
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> McpBindingOut:
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

    # A `builtin` binding toggles built-in tools per agent; if it names no known
    # tool (in `reference` or `allowed_tools`) it would silently disable ALL of
    # them for the agent (the gate only defaults-on when there are zero builtin
    # bindings). Reject it at creation rather than let it become a dead binding.
    if body.source == "builtin":
        from contexts.agents.application.runtime.builtin_tools import BUILTIN_TOOL_NAMES

        named = {body.reference, *body.allowed_tools} & BUILTIN_TOOL_NAMES
        if not named:
            raise HTTPException(
                status_code=422,
                detail=f"builtin MCP binding must name one of {sorted(BUILTIN_TOOL_NAMES)} "
                "in reference or allowed_tools",
            )

    binding = await service.add_mcp_binding(
        agent_id=agent_id,
        source=McpSource(body.source),
        reference=body.reference,
        allowed_tools=body.allowed_tools,
        config=body.config,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_binding_out(binding)


@agent_router.patch("/{agent_id}/mcp/{binding_id}")
async def patch_mcp_binding(
    body: McpBindingPatchIn,
    agent_id: uuid.UUID = Path(...),
    binding_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> McpBindingOut:
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

    binding = await service.patch_mcp_binding(
        agent_id=agent_id,
        binding_id=binding_id,
        allowed_tools=body.allowed_tools,
        config=body.config,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_binding_out(binding)


@agent_router.delete(
    "/{agent_id}/mcp/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_mcp_binding(
    agent_id: uuid.UUID = Path(...),
    binding_id: uuid.UUID = Path(...),
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

    await service.remove_mcp_binding(
        agent_id=agent_id,
        binding_id=binding_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["agent_router", "project_router"]
