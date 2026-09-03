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

from app.api.v1.deps import PaginationParams, require_if_match
from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.models import (
    MAX_CONTEXT_TOKEN_CAP,
    MAX_SKILL_INDEX_TOKEN_CAP,
    AgentDraft,
    AgentEffort,
    AgentModelHint,
    AgentToolType,
    ContextMode,
)
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.orchestration.domain.models import (
    AUTOSTOP_HARD_CAP,
    N_MAX,
    N_MIN,
    SUBAGENT_MAX_CONCURRENT_HARD,
    T_MINUTES_MAX,
    T_MINUTES_MIN,
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
from shared_kernel.type_guards import is_plain_int
from shared_kernel.validation import BoundedConfig

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


# API-7: upper bounds on free-text / list fields — an unbounded system prompt
# or tool list lets a single request drive memory / DB load.
_MAX_SYSTEM_PROMPT = 100_000
_MAX_REFERENCE = 2_000
_MAX_ALLOWED_TOOLS = 200
# A pack key is lowercase words joined by hyphens; the loader's own anchored guard
# is the authority, this only stops an absurd path segment reaching it.
_MAX_PACK_KEY = 100

# `agents.seed` is an int4 column; bound the input to its range so an oversized
# integer is rejected at the API (422) instead of overflowing the column (500).
_SEED_MIN = -(2**31)
_SEED_MAX = 2**31 - 1

# Lower floor for the two `_default_below_one`-resolved autostop fields
# (`contexts.orchestration.domain.models`); their own upper bound is the shared
# hard cap. Restated here rather than imported since the domain module has no
# constant for it — `_default_below_one`'s `value < 1` check is the source.
_AUTOSTOP_FLOOR = 1
_REFRESH_HOURS_FLOOR = 1


def _check_wakeup_int(value: Any, *, field: str, minimum: int, maximum: int | None) -> None:
    """Type/range-check one numeric leaf of `wakeup_config` (R15.07/R15.09/R28.12).

    A wrong type (including `bool`, Q-7) or an out-of-range value is refused with
    422 naming the field, instead of being silently persisted and resolved to a
    default only once the tolerant domain parser reads it back
    (2026-07-27-wakeup-config-type-validation). Raising `ValueError` here is what
    FastAPI turns into the 422 -- mirrors `_cap_config`/`_cap_auth` below.
    """
    if not is_plain_int(value):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ValueError(f"{field} must be {bound}")


def _check_wakeup_bool(value: Any, *, field: str) -> None:
    """Type-check one boolean leaf of `wakeup_config`
    (2026-07-28-wakeup-config-validation-and-patch-semantics F-2).

    `bool("false")` is `True` in Python; a wrong-typed `enabled`/`allow_self_open`
    coerced that way would silently enable what the caller meant to disable, with
    no error raised anywhere. Mirrors `_check_wakeup_int`'s reject-at-the-boundary
    contract.
    """
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")


def _check_wakeup_container(value: Any, *, field: str) -> None:
    """Type-check one dict-shaped container leaf of `wakeup_config` (F-1/F-3).

    A truthy non-dict `triggers`/`soft_bounds` used to skip validation entirely
    -- the `isinstance` guard around each block failed open instead of rejecting
    -- reaching `WakeupConfig.from_dict` unguarded (an unhandled `AttributeError`
    downstream) or, for `soft_bounds`, persisting indefinitely once the additive
    merge stopped re-touching an absent key. `None` (the caller deleting the key
    under the additive merge) is not checked here -- only a present, non-dict,
    non-`None` value is wrong.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")


def _validate_wakeup_config(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate the documented numeric leaves of `wakeup_config`, leaving every
    other key -- root-level or nested -- untouched so the column stays free-form
    (Q-3: forced by 2026-07-27-wakeup-config-key-preservation's additive merge,
    which requires unmodelled keys to survive)."""
    if value is None:
        return value
    triggers = value.get("triggers")
    if triggers is not None:
        _check_wakeup_container(triggers, field="wakeup_config.triggers")
    if isinstance(triggers, dict):
        enm = triggers.get("every_n_messages")
        if isinstance(enm, dict):
            if "n" in enm:
                _check_wakeup_int(
                    enm["n"], field="wakeup_config.triggers.every_n_messages.n", minimum=N_MIN, maximum=N_MAX
                )
            if "enabled" in enm:
                _check_wakeup_bool(enm["enabled"], field="wakeup_config.triggers.every_n_messages.enabled")
        sm = triggers.get("silence_minutes")
        if isinstance(sm, dict):
            if "t_minutes" in sm:
                _check_wakeup_int(
                    sm["t_minutes"],
                    field="wakeup_config.triggers.silence_minutes.t_minutes",
                    minimum=T_MINUTES_MIN,
                    maximum=T_MINUTES_MAX,
                )
            if "enabled" in sm:
                _check_wakeup_bool(sm["enabled"], field="wakeup_config.triggers.silence_minutes.enabled")
            for key in ("autostop_rounds", "observer_autostop_rounds", "autostop_max_default"):
                if key in sm:
                    _check_wakeup_int(
                        sm[key],
                        field=f"wakeup_config.triggers.silence_minutes.{key}",
                        minimum=_AUTOSTOP_FLOOR,
                        maximum=AUTOSTOP_HARD_CAP,
                    )
        co = triggers.get("call_only")
        if isinstance(co, dict) and "enabled" in co:
            _check_wakeup_bool(co["enabled"], field="wakeup_config.triggers.call_only.enabled")
    if "refresh_every_hours" in value:
        _check_wakeup_int(
            value["refresh_every_hours"],
            field="wakeup_config.refresh_every_hours",
            minimum=_REFRESH_HOURS_FLOOR,
            maximum=None,
        )
    if "allow_self_open" in value:
        _check_wakeup_bool(value["allow_self_open"], field="wakeup_config.allow_self_open")
    soft_bounds = value.get("soft_bounds")
    if soft_bounds is not None:
        _check_wakeup_container(soft_bounds, field="wakeup_config.soft_bounds")
    if isinstance(soft_bounds, dict):
        for key in ("n_min", "n_max"):
            if key in soft_bounds:
                _check_wakeup_int(
                    soft_bounds[key], field=f"wakeup_config.soft_bounds.{key}", minimum=N_MIN, maximum=N_MAX
                )
        for key in ("t_minutes_min", "t_minutes_max"):
            if key in soft_bounds:
                _check_wakeup_int(
                    soft_bounds[key],
                    field=f"wakeup_config.soft_bounds.{key}",
                    minimum=T_MINUTES_MIN,
                    maximum=T_MINUTES_MAX,
                )
    return value


# R15.20's hard cap is shared with the domain (SUBAGENT_MAX_CONCURRENT_HARD);
# the floor has no domain constant since 0 was never a valid value anywhere,
# only an unvalidated default (workflow-capability-enforcement spec §7.6).
_MAX_ALIVE_SUBAGENTS_MIN = 1


def _validate_workflow_capabilities(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate the four documented `workflow_capabilities` keys
    (docs/UI/06-agents.md:422-431), leaving every other key untouched so the
    column stays free-form (same additive-merge contract as `wakeup_config` —
    `agent_service.py` merges via `merge_json_config`, where an explicit `null`
    clears a key rather than erroring, which is how the frontend signals
    "not applicable" by sending `max_alive_subagents: null` when
    `can_create_subagent` is false).

    `BoundedConfig` alone bounds size/depth, not shape, which is what let both
    `{}` and `{"max_alive_subagents": 0}` round-trip unvalidated before this.

    **Per-field only.** The cross-field rule — a bound is required once
    `can_create_subagent` is true — lives in `AgentService`, applied to the
    merged value, because this validator sees a PATCH fragment and neither key
    is reliably in it. Deciding it here rejected valid patches and admitted
    invalid ones; see `_assert_capability_invariants`.
    """
    if value is None:
        return value
    for key in ("can_instruct", "can_approve", "can_create_subagent"):
        if key in value:
            _check_wakeup_bool(value[key], field=f"workflow_capabilities.{key}")

    max_alive = value.get("max_alive_subagents")
    if max_alive is not None:
        _check_wakeup_int(
            max_alive,
            field="workflow_capabilities.max_alive_subagents",
            minimum=_MAX_ALIVE_SUBAGENTS_MIN,
            maximum=SUBAGENT_MAX_CONCURRENT_HARD,
        )
    return value


class AgentCreateIn(BaseModel):
    model_config = {"protected_namespaces": ()}

    name: str = Field(min_length=1, max_length=200)
    model_hint: Literal["claude", "openai", "gemini"]
    model_id: str | None = Field(default=None, max_length=200)
    effort: AgentEffort | None = None
    key_group_id: uuid.UUID

    @field_validator("model_id", mode="before")
    @classmethod
    def _strip_model_id(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    system_prompt: str = Field(default="", max_length=_MAX_SYSTEM_PROMPT)
    rag_config_id: uuid.UUID | None = None
    knowmap_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] = "general"
    context_token_cap: int | None = Field(default=None, gt=0, le=MAX_CONTEXT_TOKEN_CAP)
    skill_index_token_cap: int | None = Field(default=None, gt=0, le=MAX_SKILL_INDEX_TOKEN_CAP)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = Field(default=None, ge=_SEED_MIN, le=_SEED_MAX)
    a2a_enabled: bool = False
    wakeup_config: BoundedConfig = Field(default_factory=dict)
    workflow_capabilities: BoundedConfig = Field(default_factory=dict)

    @field_validator("wakeup_config")
    @classmethod
    def _check_wakeup_config(cls, v: dict[str, Any]) -> dict[str, Any]:
        result = _validate_wakeup_config(v)
        assert result is not None  # non-Optional field: input is never None
        return result

    @field_validator("workflow_capabilities")
    @classmethod
    def _check_workflow_capabilities(cls, v: dict[str, Any]) -> dict[str, Any]:
        result = _validate_workflow_capabilities(v)
        assert result is not None  # non-Optional field: input is never None
        return result


class AgentPatchIn(BaseModel):
    # Every field is optional; missing → no change.
    # For nullable fields (`rag_config_id`, `context_token_cap`) send `null`
    # explicitly to clear.
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_hint: Literal["claude", "openai", "gemini"] | None = None
    model_id: str | None = Field(default=None, max_length=200)
    effort: AgentEffort | None = None
    key_group_id: uuid.UUID | None = None

    @field_validator("model_id", mode="before")
    @classmethod
    def _strip_model_id(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    system_prompt: str | None = Field(default=None, max_length=_MAX_SYSTEM_PROMPT)
    rag_config_id: uuid.UUID | None = None
    knowmap_config_id: uuid.UUID | None = None
    context_mode: Literal["general", "compact"] | None = None
    context_token_cap: int | None = Field(default=None, gt=0, le=MAX_CONTEXT_TOKEN_CAP)
    skill_index_token_cap: int | None = Field(default=None, gt=0, le=MAX_SKILL_INDEX_TOKEN_CAP)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = Field(default=None, ge=_SEED_MIN, le=_SEED_MAX)
    a2a_enabled: bool | None = None
    wakeup_config: BoundedConfig | None = None
    workflow_capabilities: BoundedConfig | None = None

    @field_validator("wakeup_config")
    @classmethod
    def _check_wakeup_config(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_wakeup_config(v)

    @field_validator("workflow_capabilities")
    @classmethod
    def _check_workflow_capabilities(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_workflow_capabilities(v)


class AgentOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    model_hint: AgentModelHint
    model_id: str | None
    effort: AgentEffort | None
    key_group_id: uuid.UUID
    system_prompt: str
    rag_config_id: uuid.UUID | None
    knowmap_config_id: uuid.UUID | None
    context_mode: ContextMode
    context_token_cap: int | None
    skill_index_token_cap: int | None
    temperature: float | None
    top_p: float | None
    seed: int | None
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
        model_hint=a.model_hint,
        model_id=a.model_id,
        effort=a.effort,
        key_group_id=a.key_group_id,
        system_prompt=a.system_prompt,
        rag_config_id=a.rag_config_id,
        knowmap_config_id=a.knowmap_config_id,
        context_mode=a.context_mode,
        context_token_cap=a.context_token_cap,
        skill_index_token_cap=a.skill_index_token_cap,
        temperature=a.temperature,
        top_p=a.top_p,
        seed=a.seed,
        a2a_enabled=a.a2a_enabled,
        wakeup_config=a.wakeup_config,
        workflow_capabilities=a.workflow_capabilities,
        version=a.version,
        created_at=a.created_at.isoformat(),
        deleted_at=a.deleted_at.isoformat() if a.deleted_at else None,
    )


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


class AgentNameOut(BaseModel):
    """An agent reduced to what a label needs.

    `AgentOut` carries `system_prompt`, bounded at 100k characters. Every caller
    that only wanted a name paid for that: the chatroom builds an id-to-name map
    from this collection on every room open, so a project near the pagination
    ceiling turned opening a room into a multi-megabyte response for two fields
    per row.
    """

    id: uuid.UUID
    name: str


@project_router.get("/names")
async def list_project_agent_names(
    project_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[AgentNameOut]:
    """The same rows as `GET ""`, in the same order, projected to id and name.

    Deliberately the same membership gate: a name is not more sensitive than the
    listing it is drawn from, and a second, looser gate on the same rows is how
    an authorization surface drifts apart from itself.
    """
    service = AgentService(db)
    rows = await service.names_for_project(
        project_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return [AgentNameOut(id=agent_id, name=name) for agent_id, name in rows]


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
        effort=AgentEffort(body.effort) if body.effort else None,
        key_group_id=body.key_group_id,
        system_prompt=body.system_prompt,
        rag_config_id=body.rag_config_id,
        knowmap_config_id=body.knowmap_config_id,
        context_mode=ContextMode(body.context_mode),
        context_token_cap=body.context_token_cap,
        skill_index_token_cap=body.skill_index_token_cap,
        temperature=body.temperature,
        top_p=body.top_p,
        seed=body.seed,
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
# Shipped example packs ([R30.35])
# ---------------------------------------------------------------------------


class ExamplePackAgentOut(BaseModel):
    """One agent a pack would install, and whether this project already has it."""

    model_config = {"protected_namespaces": ()}

    key: str
    name: str
    # None means the agent is not meant for a class chatroom (the design agent).
    # Advisory: installing binds no room, so nothing enforces it.
    room_role: Literal["normal", "observer"] | None
    preferred_model_hint: str
    binds_activity_types: list[str]
    # Whether this agent's prompt is written to hold delegated activity control
    # ([R30.37], [R30.35]). Advisory, exactly like `room_role` above: installing
    # binds no room, so there is nothing to grant it on — the room creator grants
    # it per room after binding, and the install dialog says so.
    may_control_activities: bool
    installed: bool


class ExamplePackOut(BaseModel):
    pack_key: str
    title: str
    source: str
    # The shipped course this pack is written against, so the dialog can say which
    # activity types the prompts assume.
    for_course: str
    group_name: str
    agents: list[ExamplePackAgentOut]
    fully_installed: bool


class ExamplePackInstallIn(BaseModel):
    # `model_hint` collides with Pydantic's protected `model_` namespace, as
    # AgentOut already documents by carrying the same override.
    model_config = {"protected_namespaces": ()}

    key_group_id: uuid.UUID
    # None means "resolve from the key group": a pack names a preferred provider
    # but never a key, and a project holding a different vendor's key must still
    # be able to install the example.
    model_hint: Literal["claude", "openai", "gemini"] | None = None


class InstalledPackAgentOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    key: str
    name: str
    agent_id: uuid.UUID
    # The hint actually used, which is not always the pack's preference -- reported
    # so the installer sees what was chosen rather than assuming the pack decided.
    model_hint: str


class ExamplePackInstallReportOut(BaseModel):
    """What one install created and what was already there.

    Both lists rather than a count: install is idempotent by agent name, so
    "nothing created" is a normal, successful outcome the installer needs to see
    the reason for.
    """

    pack_key: str
    created: list[InstalledPackAgentOut]
    already_present: list[str]
    group_id: uuid.UUID
    # Whether `group_id` names a group this install created. The group is matched
    # by name, so a re-install after the owner renamed it creates a second one --
    # a run that creates nothing else and would otherwise be reported as having
    # created nothing at all.
    group_created: bool


@project_router.get("/example-packs")
async def list_agent_example_packs(
    project_id: uuid.UUID = Path(...),
    _=Depends(
        require(
            Capability.RESOURCE_CREATE_EDIT,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> list[ExamplePackOut]:
    """The shipped packs and this project's install state ([R30.35]).

    Gated on `RESOURCE_CREATE_EDIT` rather than plain membership, matching
    `create_agent`: the only thing this listing is for is deciding what to
    install, and installing creates agents.
    """
    packs = await AgentsFacade(db).list_example_packs(project_id)
    return [
        ExamplePackOut(
            pack_key=pack.pack_key,
            title=pack.title,
            source=pack.source,
            for_course=pack.for_course,
            group_name=pack.group_name,
            agents=[
                ExamplePackAgentOut(
                    key=a.key,
                    name=a.name,
                    room_role=a.room_role,
                    preferred_model_hint=a.preferred_model_hint.value,
                    binds_activity_types=list(a.binds_activity_types),
                    may_control_activities=a.may_control_activities,
                    installed=a.installed,
                )
                for a in pack.agents
            ],
            fully_installed=pack.fully_installed,
        )
        for pack in packs
    ]


@project_router.post("/example-packs/{pack_key}/install")
async def install_agent_example_pack(
    body: ExamplePackInstallIn,
    project_id: uuid.UUID = Path(...),
    pack_key: str = Path(..., max_length=_MAX_PACK_KEY),
    principal: Principal = Depends(current_principal),
    ctx: RequestContext = Depends(current_context),
    _=Depends(
        require(
            Capability.RESOURCE_CREATE_EDIT,
            scope_from_path(project_param="project_id"),
        )
    ),
    db: AsyncSession = Depends(db_session),
) -> ExamplePackInstallReportOut:
    """Instantiate a shipped pack into this project ([R30.35]).

    Creates agents and one agent group, nothing else: no chatroom, no room
    binding, no activity started. `pack_key` is a client-supplied path segment,
    which is what makes the loader's anchored traversal guard load-bearing here
    rather than merely tidy.
    """
    report = await AgentsFacade(db).install_example_pack(
        project_id=project_id,
        pack_key=pack_key,
        key_group_id=body.key_group_id,
        model_hint=AgentModelHint(body.model_hint) if body.model_hint else None,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return ExamplePackInstallReportOut(
        pack_key=report.pack_key,
        created=[
            InstalledPackAgentOut(
                key=a.key,
                name=a.name,
                agent_id=a.agent_id,
                model_hint=a.model_hint.value,
            )
            for a in report.created
        ],
        already_present=list(report.already_present),
        group_id=report.group_id,
        group_created=report.group_created,
    )


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
    expected = require_if_match(if_match)
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
        effort=AgentEffort(fields["effort"]) if fields.get("effort") else None,
        key_group_id=fields.get("key_group_id"),
        system_prompt=fields.get("system_prompt"),
        rag_config_id=fields.get("rag_config_id"),
        knowmap_config_id=fields.get("knowmap_config_id"),
        context_mode=(ContextMode(fields["context_mode"]) if "context_mode" in fields else None),
        context_token_cap=fields.get("context_token_cap"),
        skill_index_token_cap=fields.get("skill_index_token_cap"),
        temperature=fields.get("temperature"),
        top_p=fields.get("top_p"),
        seed=fields.get("seed"),
        a2a_enabled=fields.get("a2a_enabled"),
        wakeup_config=fields.get("wakeup_config"),
        workflow_capabilities=fields.get("workflow_capabilities"),
        # Distinguish "explicit null" from "omitted".
        clear_model_id="model_id" in fields and fields["model_id"] is None,
        clear_effort="effort" in fields and fields["effort"] is None,
        clear_rag_config="rag_config_id" in fields and fields["rag_config_id"] is None,
        clear_knowmap_config="knowmap_config_id" in fields and fields["knowmap_config_id"] is None,
        clear_context_token_cap=("context_token_cap" in fields and fields["context_token_cap"] is None),
        clear_skill_index_token_cap=(
            "skill_index_token_cap" in fields and fields["skill_index_token_cap"] is None
        ),
        clear_temperature=("temperature" in fields and fields["temperature"] is None),
        clear_top_p=("top_p" in fields and fields["top_p"] is None),
        clear_seed=("seed" in fields and fields["seed"] is None),
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
    expected = require_if_match(if_match)
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

    from app.api.v1._graphrag_owner_cascade import purge_owner_graph_configs_external
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    # Enumerate the GraphRAG configs this agent's deletion actually retires
    # before the soft-deletes so their external stores can be purged after
    # commit (DOM-4). `list_graph_configs_for_agent` only returns a shared
    # (agent_group-owned) config when removing this agent leaves the group with
    # zero other live members — deleting one member of a multi-member group must
    # not destroy the Concept Map the other members still depend on.
    facade = KnowledgeFacade(db)
    graph_configs = await facade.list_graph_configs_for_agent(agent_id)

    await service.soft_delete(
        agent_id=agent_id,
        expected_version=expected,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    for cfg in graph_configs:
        await facade.soft_delete_graph_config(
            cfg.id,
            actor_user_id=principal.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )

    # DOM-4: commit the agent + config soft-deletes (and the agent.deleted /
    # graphrag.deleted audit rows) before touching any external store.
    await db.commit()

    await purge_owner_graph_configs_external(
        db,
        configs=graph_configs,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
        extra_metadata={"agent_id": str(agent_id)},
    )


# ---------------------------------------------------------------------------
# Unified Agent Tools surface (Phase A — /api/agents/{id}/tools)
# ---------------------------------------------------------------------------

_MAX_DISPLAY_NAME = 200


class AgentToolOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    tool_type: AgentToolType
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
    config: BoundedConfig = Field(default_factory=dict)
    auth: BoundedConfig | None = None

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
    config: BoundedConfig | None = None
    auth: BoundedConfig | None = None
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
        tool_type=t.tool_type,
        enabled=t.enabled,
        display_name=t.display_name,
        config=cfg,
        config_warnings=config_warnings or [],
        created_at=t.created_at.isoformat(),
    )


async def _tool_warnings(db: AsyncSession, project_id: uuid.UUID, tool: Any) -> list[str]:
    """Non-blocking config warnings for the tools whose egress the project's
    allowlist gates: a local function's configured URL, the project's active
    search-key provider (R12.16 -- normally self-healing via activation, but
    an operator can remove the seeded host afterwards), and a URL-sourced
    ``hosted_mcp`` binding's reference. Package-sourced ``hosted_mcp`` has no
    single knowable host to check and is skipped."""
    from contexts.agents.application.runtime.builtin_tools import function_egress_allowed
    from contexts.agents.domain.models import AgentToolType

    if not hasattr(tool, "tool_type"):
        return []

    if tool.tool_type == AgentToolType.LOCAL_FUNCTION:
        http = (tool.config or {}).get("http", {})
        url = http.get("url", "")
        if not url:
            return []
        host, allowed = await function_egress_allowed(db, project_id=project_id, url=url)
        if host and not allowed:
            return [f"host {host} is not on the project egress allowlist"]
        return []

    if tool.tool_type == AgentToolType.HOSTED_WEB_SEARCH:
        from contexts.keys.domain.search import SEARCH_PROVIDER_HOSTS
        from contexts.keys.infrastructure.search_repository import SearchKeyRepository

        keys = await SearchKeyRepository(db).list_for_project(project_id)
        active = next((k for k in keys if k.is_active), None)
        if active is None:
            return []
        host = SEARCH_PROVIDER_HOSTS[active.provider]
        _, allowed = await function_egress_allowed(db, project_id=project_id, url=f"https://{host}")
        if not allowed:
            return [f"host {host} is not on the project egress allowlist"]
        return []

    if tool.tool_type == AgentToolType.HOSTED_MCP:
        cfg = tool.config or {}
        warnings: list[str] = []

        # Capture-staleness advisories (Q-7 / Fix Design §7 piece 2): a tool
        # bound in config but absent from (or never in) the last capture must
        # surface, never silently drop -- the model still gets the permissive
        # fallback schema for it, this is purely informational.
        allowed_tools = [n for n in cfg.get("allowed_tools") or [] if isinstance(n, str) and n]
        if allowed_tools:
            if tool.mcp_captured_at is None:
                warnings.append(
                    "MCP tool contract has not been captured yet; using the permissive "
                    "fallback schema until Test is run."
                )
            else:
                captured_names = {spec.name for spec in tool.mcp_captured_tools}
                for name in allowed_tools:
                    if name not in captured_names:
                        warnings.append(
                            f"tool {name!r} was not found in the last capture; it may no "
                            "longer be offered by the server. Re-run Test to confirm."
                        )

        if cfg.get("source") == "url":
            reference = str(cfg.get("reference", ""))
            if reference:
                host, allowed = await function_egress_allowed(db, project_id=project_id, url=reference)
                if host and not allowed:
                    warnings.append(f"host {host} is not on the project egress allowlist")

        return warnings

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
        warnings = await _tool_warnings(db, agent.project_id, t)
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
    warnings = await _tool_warnings(db, agent.project_id, tool)
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
    warnings = await _tool_warnings(db, agent.project_id, tool)
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
    # Non-blocking advisories from a successful hosted_mcp re-capture (e.g. a
    # schema exceeded the size/property/$ref caps and fell back to permissive).
    warnings: list[str] = []


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
        raise HTTPException(status_code=422, detail="only hosted_mcp and local_function tools can be tested")

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
        warnings=list(result.warnings),
    )


__all__ = ["agent_router", "project_router"]
