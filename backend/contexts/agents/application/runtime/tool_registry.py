"""Per-turn tool registry (K.2).

There is no shared tool abstraction in the codebase — each built-in tool has a
bespoke shape — so this module defines the uniform surface the turn engine's
tool-use loop needs: ``Tool{name, description, input_schema, invoke(args)}`` and
a ``ToolRegistry`` that exposes provider-neutral specs and dispatches calls.

Wired here (no external infra): ``update_wakeup`` (R15.06 — clamp + audit happen
inside ``WakeupService``) and ``load_prompt_section`` (R9.06 — served from the
per-turn ``SectionCache`` over the lazy prompt's body bank). ``web_search`` /
``file`` / ``code_exec`` are injected as ``extra`` tools by their own wiring
(web-search DI; sandbox lands in K.5) so this module stays infra-free.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from contexts.agents.application.prompt_loader import (
    LOAD_SECTION_TOOL_SPEC,
    LazyPrompt,
    SectionCache,
)

ToolInvoke = Callable[[dict[str, Any]], Awaitable["ToolResult"]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool hands back to the model (text), plus an error flag."""

    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: ToolInvoke

    def spec(self) -> dict[str, Any]:
        """Provider-neutral tool definition the adapters translate (K.1)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Name → Tool table for one turn. Dispatch never raises into the loop."""

    def __init__(self, tools: list[Tool]) -> None:
        self._by_name: dict[str, Tool] = {t.name: t for t in tools}

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._by_name.values()]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._by_name.get(name)
        if tool is None:
            return ToolResult(content=f"Unknown tool {name!r}.", is_error=True)
        try:
            return await tool.invoke(args)
        except Exception as exc:  # a tool failure must not abort the turn
            return ToolResult(content=f"Tool {name!r} failed: {exc}", is_error=True)

    def __len__(self) -> int:
        return len(self._by_name)

    def __bool__(self) -> bool:
        return bool(self._by_name)


# --------------------------------------------------------------------------- #
# Built-in tool builders                                                       #
# --------------------------------------------------------------------------- #

_UPDATE_WAKEUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "every_n_messages": {
            "type": "integer",
            "description": "Fire a wake-up after every N user messages (clamped to 1..1000).",
        },
        "silence_minutes": {
            "type": "integer",
            "description": "Fire after T minutes of silence (clamped to 1..1440).",
        },
    },
    "additionalProperties": False,
}


def build_update_wakeup_tool(db: Any, *, agent_id: uuid.UUID) -> Tool:
    """R15.06 — agent self-adjusts its own wake-up cadence (hard/soft clamp R15.07)."""

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        cfg = await OrchestrationFacade(db).update_wakeup(
            agent_id=agent_id,
            every_n_messages=_opt_int(args.get("every_n_messages")),
            silence_minutes=_opt_int(args.get("silence_minutes")),
            actor_agent_id=agent_id,
        )
        return ToolResult(content=json.dumps(cfg.to_dict()))

    return Tool(
        name="update_wakeup",
        description=(
            "Adjust your own wake-up triggers: how many user messages or how many "
            "minutes of silence should fire your next turn. Values are clamped to "
            "safe bounds."
        ),
        input_schema=_UPDATE_WAKEUP_SCHEMA,
        invoke=_invoke,
    )


def build_load_prompt_section_tool(prompt: LazyPrompt, cache: SectionCache) -> Tool:
    """R9.06 — fetch a lazy prompt section body on demand (per-turn cached, R9.07)."""

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        section_id = str(args.get("id", ""))
        cached = cache.get(section_id)
        if cached is not None:
            return ToolResult(content=cached)
        body = prompt.bodies.get(section_id)
        if body is None:
            return ToolResult(content=f"No prompt section with id {section_id!r}.", is_error=True)
        cache.put(section_id, body)
        return ToolResult(content=body)

    return Tool(
        name=str(LOAD_SECTION_TOOL_SPEC["name"]),
        description=str(LOAD_SECTION_TOOL_SPEC["description"]),
        input_schema=dict(LOAD_SECTION_TOOL_SPEC["input_schema"]),
        invoke=_invoke,
    )


_CAST_APPROVAL_VOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_id": {
            "type": "string",
            "description": "The approval gate id you were notified about.",
        },
        "vote": {
            "type": "boolean",
            "description": "true to approve, false to reject.",
        },
        "rationale": {
            "type": "string",
            "description": "Optional short reason for your vote.",
        },
    },
    "required": ["approval_id", "vote"],
    "additionalProperties": False,
}


def build_cast_approval_vote_tool(
    db: Any,
    *,
    agent_id: uuid.UUID,
    allowed_approvals: dict[uuid.UUID, uuid.UUID | None],
) -> Tool:
    """R15.10–R15.14 — let an approver agent vote on a gate it was notified of.

    Scoped to the keys of ``allowed_approvals`` (the gates whose notifications
    this turn drained) so an agent cannot vote on an arbitrary gate id it
    guessed. The mapped value carries each gate's originating ``chatroom_id`` (or
    None for a headless/workflow gate) so resolution publishes ``approval.resolved``
    on the right room channel."""

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        raw = str(args.get("approval_id", ""))
        try:
            approval_id = uuid.UUID(raw)
        except ValueError:
            return ToolResult(content=f"Invalid approval_id {raw!r}.", is_error=True)
        if approval_id not in allowed_approvals:
            return ToolResult(
                content=f"approval_id {raw} is not one you were asked to vote on.",
                is_error=True,
            )
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        ballot = await OrchestrationFacade(db).cast_approval_vote(
            approval_id=approval_id,
            voter_agent_id=agent_id,
            vote=bool(args.get("vote")),
            rationale=(str(args["rationale"]) if args.get("rationale") else None),
            chatroom_id=allowed_approvals[approval_id],
        )
        return ToolResult(
            content=json.dumps(
                {"approval_id": raw, "vote": ballot.vote, "recorded": True}
            )
        )

    return Tool(
        name="cast_approval_vote",
        description=(
            "Cast your approval vote on a gate you were notified about. Provide the "
            "approval_id from the notification, a boolean vote, and an optional rationale."
        ),
        input_schema=_CAST_APPROVAL_VOTE_SCHEMA,
        invoke=_invoke,
    )


def build_registry(
    db: Any,
    *,
    agent_id: uuid.UUID,
    lazy_prompt: LazyPrompt | None = None,
    section_cache: SectionCache | None = None,
    extra: list[Tool] | None = None,
) -> ToolRegistry:
    """Assemble the per-turn tool table for ``agent_id``."""
    tools: list[Tool] = [build_update_wakeup_tool(db, agent_id=agent_id)]
    if lazy_prompt is not None and section_cache is not None:
        tools.append(build_load_prompt_section_tool(lazy_prompt, section_cache))
    if extra:
        tools.extend(extra)
    return ToolRegistry(tools)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_cast_approval_vote_tool",
    "build_load_prompt_section_tool",
    "build_registry",
    "build_update_wakeup_tool",
]
