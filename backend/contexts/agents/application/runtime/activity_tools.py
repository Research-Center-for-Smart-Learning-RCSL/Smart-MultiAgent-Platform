"""The two room-scoped tools a delegated agent gets on its turn ([R30.37]).

Separate from ``builtin_tools`` for two reasons: the dependency on the activities
context stays in one file, and these are the only tools in the runtime whose
*source* is a room grant rather than an ``agent_tools`` row.

WHAT MAKES THIS SAFE TO EXIST AT ALL
------------------------------------
The only path from model output to a class-visible activation is a structured tool
call whose one argument is an ``enum`` built at turn-assembly time from the types
the room creator selected. A participant cannot widen that set, and a participant's
text cannot become an argument value — no client-supplied identifier crosses this
boundary at all. The residual exposure is that a participant may *persuade* a
granted agent to call the tool at a bad moment; that is bounded by the allowlist
and addressed in the agent's prompt, not here.

Both tools reach the same ``ActivitiesFacade`` methods the HTTP routes do, so type
reachability ([R30.33]) and the platform governance policy ([R30.30]) are enforced
once, for both paths. There is deliberately no second implementation of either gate.

TRANSACTION AND BROADCAST
-------------------------
Both write on the turn's own session and do **not** commit, exactly like
``build_update_wakeup_tool``. Nothing is published from here: each success appends a
descriptor to ``event_sink``, which the turn engine drains after its commit. A turn
that fails rolls back and the sink is discarded with it, so a room is never told
about an activation that no longer exists.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ActivityType
from contexts.agents.application.runtime.tool_registry import Tool, ToolResult, clip_tool_output
from contexts.agents.domain.models import Agent
from contexts.conversation.interfaces.facade import ActivityControlGrant, ConversationFacade

logger = logging.getLogger(__name__)

# Descriptor kinds the turn engine's drain dispatches on. Strings rather than an
# enum because the sink is a plain ``list[dict]``, matching ``artifact_sink``.
EVENT_STARTED = "activation_started"
EVENT_ENDED = "activation_ended"


@dataclass(frozen=True, slots=True)
class ActivityControlContext:
    """Everything the two tools need, resolved once per turn.

    Constructed only by :func:`resolve_activity_control`, and only for a binding
    that actually holds a live grant — so holding one of these *is* the
    authorization. ``allowed_types`` has already passed the reachability gate for
    ``project_id``, so an allowlist entry naming a deleted or unreachable type is
    simply absent from it (AC-6) rather than an error at call time.
    """

    chatroom_id: uuid.UUID
    project_id: uuid.UUID
    grant: ActivityControlGrant
    allowed_types: tuple[ActivityType, ...]


async def resolve_activity_control(
    db: AsyncSession, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID
) -> ActivityControlContext | None:
    """The room's delegation to this agent, resolved for this turn, or ``None``.

    **Fails closed on everything.** No grant, no room, no project, no resolvable
    type, or any exception at all yields ``None`` and therefore no tools. An error
    reading the grant must never be read as authorization, which is why the
    catch-all is here and not merely at the caller.

    Each allowlisted id goes through ``resolve_type_for_project`` — the same gate
    the HTTP path applies — so a stale id degrades to "that activity is not
    offered", never to a cross-tenant or deleted-type activation. A grant whose
    every id has gone stale yields ``None``: two tools over an empty enum are worse
    than no tools, because the model would be told it may act and then find no
    legal argument.
    """
    try:
        conversation = ConversationFacade(db)
        grant = await conversation.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)
        if grant is None:
            return None
        project_id = await conversation.project_id_for_chatroom(chatroom_id)
        if project_id is None:
            return None
        allowed = await _resolve_allowed_types(db, project_id=project_id, grant=grant)
        if not allowed:
            return None
        return ActivityControlContext(
            chatroom_id=chatroom_id,
            project_id=project_id,
            grant=grant,
            allowed_types=allowed,
        )
    except Exception:
        logger.warning(
            "activity control resolution failed for agent %s in room %s; offering no tools",
            agent_id,
            chatroom_id,
            exc_info=True,
        )
        return None


async def _resolve_allowed_types(
    db: AsyncSession, *, project_id: uuid.UUID, grant: ActivityControlGrant
) -> tuple[ActivityType, ...]:
    """The allowlisted types this project may actually use, in the stored order.

    A single unresolvable id is logged and dropped rather than failing the set: the
    teacher's other selections are still legitimate, and the alternative is one
    deleted worksheet silently costing an agent every activity it may run.

    Only ``ActivityTypeNotFound`` is treated that way. Catching everything here
    would make the log line a diagnosis this code has not established — a database
    outage would emit one "not reachable from project" per id, with no stack and at
    INFO. Anything else propagates to :func:`resolve_activity_control`, which is
    still fail-closed (no tools) but records what actually happened.
    """
    from contexts.activities.domain.errors import ActivityTypeNotFound
    from contexts.activities.interfaces.facade import ActivitiesFacade

    facade = ActivitiesFacade(db)
    resolved: list[ActivityType] = []
    for type_id in dict.fromkeys(grant.activity_type_ids):
        try:
            resolved.append(
                await facade.resolve_type_for_project(project_id=project_id, activity_type_id=type_id)
            )
        except ActivityTypeNotFound:
            logger.info(
                "allowlisted activity type %s is not reachable from project %s; not offering it",
                type_id,
                project_id,
            )
    return tuple(resolved)


def _enum_values(allowed_types: tuple[ActivityType, ...]) -> dict[str, ActivityType]:
    """``enum value -> type``, one entry per allowed type, values unique.

    The value is the type's ``key``. [R30.02] permits a project-owned type and an
    opted-in platform type to share one key, so a key alone is not always a unique
    handle — and an ambiguous enum would let the model name a value that resolves
    to two different worksheets. Collisions get a numeric suffix, the same
    deterministic uniquifying loop ``_mcp_tool_name_from_agent_tool`` uses for
    provider tool names. Dropping the second one instead would silently remove a
    worksheet the teacher explicitly granted.
    """
    out: dict[str, ActivityType] = {}
    for activity_type in allowed_types:
        value = activity_type.key
        suffix = 2
        while value in out:
            value = f"{activity_type.key}#{suffix}"
            suffix += 1
        out[value] = activity_type
    return out


def _start_description(by_value: dict[str, ActivityType]) -> str:
    listing = "; ".join(f"{value} = {t.name}" for value, t in by_value.items())
    return (
        "Start a structured activity for everyone in this chatroom. This is a "
        "class-visible action taken on the teacher's behalf. A room runs one activity "
        "at a time, so end the current one first. Available activities: " + listing + "."
    )


def build_activity_control_tools(
    db: AsyncSession,
    *,
    agent: Agent,
    control: ActivityControlContext,
    event_sink: list[dict[str, Any]] | None = None,
) -> list[Tool]:
    """``start_activity`` and ``end_activity`` for one granted turn ([R30.37]).

    ``event_sink`` mirrors ``artifact_sink``: the tools append, the engine drains
    after its commit. Passing ``None`` builds working tools that broadcast nothing,
    which is only correct for a caller that has no post-commit seam.
    """
    by_value = _enum_values(control.allowed_types)
    return [
        _build_start_tool(db, agent=agent, control=control, by_value=by_value, event_sink=event_sink),
        _build_end_tool(db, agent=agent, control=control, event_sink=event_sink),
    ]


def _start_failure(exc: Exception) -> str:
    """A refusal the model can act on (AC-8).

    Interpolating the exception alone is not enough for the two cases that
    actually happen. ``ActivityAlreadyActive`` carries only an activation UUID, so
    a bare render tells the model "start_activity failed: 3f2a…" and it has no way
    to work out that the fix is to end the running round first. ``ActivityTypeViolatesPolicy``
    does carry a sentence, and it is passed through.

    Anything else falls back to the exception text, which is safe here because
    ``_reraise_if_infrastructure`` has already taken the class of error whose
    message could carry SQL, table names or parameter values.
    """
    from contexts.activities.domain.errors import ActivityAlreadyActive

    if isinstance(exc, ActivityAlreadyActive):
        return (
            "A different activity is already running in this room, and a room runs one at "
            "a time. End the current one before starting this one."
        )
    return f"start_activity failed: {exc}"


def _build_start_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    control: ActivityControlContext,
    by_value: dict[str, ActivityType],
    event_sink: list[dict[str, Any]] | None,
) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "activity_type_key": {
                "type": "string",
                "enum": list(by_value),
                "description": "Which activity to start. Must be one of the listed values.",
            }
        },
        "required": ["activity_type_key"],
        "additionalProperties": False,
    }

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.activities.interfaces.facade import ActivitiesFacade

        value = str(args.get("activity_type_key", ""))
        target = by_value.get(value)
        if target is None:
            # Unreachable through a schema-validating registry, and kept anyway:
            # this is the boundary that must hold even if validation is ever
            # loosened, and naming the legal values is what lets the model recover.
            return ToolResult(
                content=f"{value!r} is not one of the activities you may run: {', '.join(by_value)}.",
                is_error=True,
            )
        try:
            activation = await ActivitiesFacade(db).start_activation(
                project_id=control.project_id,
                chatroom_id=control.chatroom_id,
                activity_type_id=target.id,
                # The granting teacher, not the agent: they are the answerable
                # party, and the facilitator's per-round progress event is
                # addressed to this id with no self-healing path for it.
                started_by_user_id=control.grant.granted_by_user_id,
                actor_ip=None,
                started_by_agent_id=agent.id,
            )
        except Exception as exc:
            _reraise_if_infrastructure(exc, "start_activity")
            # A governance refusal or an already-running different activity lands
            # here, and both are things the model can act on (AC-8). No activation,
            # audit row or broadcast was produced, because the service checks
            # before it inserts.
            return ToolResult(content=_start_failure(exc), is_error=True)

        recorded = await _audit_activity_tool(
            db, agent=agent, tool_name="start_activity", chatroom_id=control.chatroom_id, ok=True
        )
        if event_sink is not None:
            event_sink.append(
                {
                    "kind": EVENT_STARTED,
                    "activation": activation,
                    "activity_type": target,
                }
            )
        body = f"Started {target.name!r} for this room. Activation id {activation.id}."
        if not recorded:
            return _marked_unrecorded(body)
        return ToolResult(content=clip_tool_output(body))

    return Tool(
        name="start_activity",
        description=_start_description(by_value),
        input_schema=schema,
        invoke=_invoke,
    )


_END_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _build_end_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    control: ActivityControlContext,
    event_sink: list[dict[str, Any]] | None,
) -> Tool:
    allowed_ids = {t.id for t in control.allowed_types}

    async def _invoke(_args: dict[str, Any]) -> ToolResult:
        from contexts.activities.interfaces.facade import ActivitiesFacade

        facade = ActivitiesFacade(db)
        try:
            active = await facade.get_active_activation(control.chatroom_id)
        except Exception as exc:
            _reraise_if_infrastructure(exc, "end_activity")
            return ToolResult(content=f"end_activity failed: {exc}", is_error=True)
        if active is None:
            return ToolResult(content="No activity is running in this room.", is_error=True)
        if active.activity_type_id not in allowed_ids:
            # AC-9. An agent trusted with one unit's worksheet must not be able to
            # cut short a round the teacher started from another unit — the grant
            # bounds what it may end exactly as it bounds what it may start.
            return ToolResult(
                content=(
                    "The activity running in this room is not one you were given control of, "
                    "so you cannot end it. Only the teacher can."
                ),
                is_error=True,
            )
        try:
            result = await facade.end_activation(
                chatroom_id=control.chatroom_id,
                activation_id=active.id,
                actor_user_id=control.grant.granted_by_user_id,
                actor_ip=None,
                ended_by_agent_id=agent.id,
            )
        except Exception as exc:
            _reraise_if_infrastructure(exc, "end_activity")
            return ToolResult(content=f"end_activity failed: {exc}", is_error=True)

        recorded = await _audit_activity_tool(
            db, agent=agent, tool_name="end_activity", chatroom_id=control.chatroom_id, ok=True
        )
        if result.transitioned and event_sink is not None:
            # Gated on `transitioned` for the same reason the HTTP route is: a
            # repeat end changed nothing, and replaying the event would tell a room
            # a round ended that had already ended.
            event_sink.append(
                {
                    "kind": EVENT_ENDED,
                    "chatroom_id": control.chatroom_id,
                    "activation_id": result.activation.id,
                }
            )
        body = (
            "Ended the running activity."
            if result.transitioned
            else "That activity had already ended; nothing changed."
        )
        if not recorded:
            return _marked_unrecorded(body)
        return ToolResult(content=clip_tool_output(body))

    return Tool(
        name="end_activity",
        description=(
            "End the structured activity currently running in this chatroom, closing every "
            "participant's session for it. Class-visible, taken on the teacher's behalf, and "
            "not reversible: a new round is a new activity. Takes no arguments."
        ),
        input_schema=_END_SCHEMA,
        invoke=_invoke,
    )


def _reraise_if_infrastructure(exc: Exception, tool: str) -> None:
    """Delegates to the shared classifier; see ``builtin_tools`` for the reasoning.

    Imported inside the function rather than at module scope because
    ``builtin_tools.build_agent_tools`` imports this module, and a top-level import
    back would close the cycle.
    """
    from contexts.agents.application.runtime.builtin_tools import _reraise_if_infrastructure as impl

    impl(exc, tool)


def _marked_unrecorded(content: str) -> ToolResult:
    """The shared "this call ran but was not recorded" result shaping.

    These two tools are side-effecting, so the existing treatment applies unchanged
    — the model is told the call ran and must not be repeated. Lazily imported for
    the same cycle reason as above.
    """
    from contexts.agents.application.runtime.builtin_tools import _marked_unrecorded as impl

    return impl(content)


async def _audit_activity_tool(
    db: AsyncSession, *, agent: Agent, tool_name: str, chatroom_id: uuid.UUID, ok: bool
) -> bool:
    """Record one delegated activity call on the ``mcp.tool_invoked`` trail.

    Not ``builtin_tools._audit_tool_invoke``: that one takes the ``AgentTool`` row a
    tool was built from and records its id and MCP reference, and these two tools
    have no such row by design ([R30.37] Q-5). ``source`` says so explicitly, so a
    reader of the trail is not left looking for a ``tool_id`` that never existed.

    ``isolated=True`` for the reason every tool audit uses it: this shares the
    turn's session, and a failed insert would abort the transaction the reply is
    persisted in. The activation itself carries its own
    ``activity.activation_started`` / ``_ended`` event, so a lost row here costs the
    per-call record, not the record that the round happened.
    """
    try:
        from shared_kernel import audit

        return await audit.emit(
            db,
            audit.AuditEvent(
                action="mcp.tool_invoked",
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "tool": tool_name,
                    "source": "activity_control_grant",
                    "chatroom_id": str(chatroom_id),
                    "ok": ok,
                },
            ),
            isolated=True,
        )
    except Exception:
        logger.error("Failed to write activity tool audit event", exc_info=True)
        return False


__all__ = [
    "EVENT_ENDED",
    "EVENT_STARTED",
    "ActivityControlContext",
    "build_activity_control_tools",
    "resolve_activity_control",
]
