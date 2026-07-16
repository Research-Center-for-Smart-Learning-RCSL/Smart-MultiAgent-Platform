"""Per-turn tool registry (K.2).

There is no shared tool abstraction in the codebase — each built-in tool has a
bespoke shape — so this module defines the uniform surface the turn engine's
tool-use loop needs: ``Tool{name, description, input_schema, invoke(args)}`` and
a ``ToolRegistry`` that exposes provider-neutral specs and dispatches calls.

Wired here (no external infra): ``update_wakeup`` (R15.06 — clamp + audit happen
inside ``WakeupService``) and ``read_skill`` (§31 — served entirely from the
turn's already-resolved snapshot, so it needs no DB either). ``web_search`` /
``file`` / ``code_exec`` are injected as ``extra`` tools by their own wiring
(web-search DI; sandbox lands in K.5) so this module stays infra-free.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from contexts.skills.domain.models import Skill
from shared_kernel.tokens import estimate_tokens

ToolInvoke = Callable[[dict[str, Any]], Awaitable["ToolResult"]]

# Per-tool output cap so a chatty tool can't blow the context window. It lives here
# rather than beside its callers in ``builtin_tools`` because it is the registry's
# contract with the turn loop, and ``read_skill`` — built in this module — has to size
# its own span against it (see :func:`_fit_skill_body`).
_MAX_TOOL_OUTPUT = 16_000


def clip_tool_output(text: str) -> str:
    """The byte-level backstop every tool's output passes through."""
    return text if len(text) <= _MAX_TOOL_OUTPUT else text[:_MAX_TOOL_OUTPUT] + "\n…[truncated]"


# Canonical set of built-in / runtime tool names a user LOCAL_FUNCTION must not
# shadow. Single source of truth: agent_service derives its reserved-name guard
# from this, and a drift test (test_builtin_tools_wiring) asserts every hosted
# built-in tool actually built carries a name listed here — so adding a new
# built-in without reserving it fails CI rather than silently allowing a user
# function to shadow it. MCP tools occupy the separate `mcp__` prefix.
BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "update_wakeup",
        "cast_approval_vote",
        "read_skill",
        "web_search",
        "code_exec",
        "file",
        "file_search",
    }
)


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


logger = logging.getLogger(__name__)


class ToolRegistry:
    """Name → Tool table for one turn. Dispatch never raises into the loop."""

    def __init__(self, tools: list[Tool]) -> None:
        # A duplicate name would silently shadow a built-in (last-wins dict),
        # so the first registration wins and any collision is dropped + logged.
        # Reserved-name validation upstream should make this unreachable; this is
        # the backstop.
        self._by_name: dict[str, Tool] = {}
        for t in tools:
            if t.name in self._by_name:
                logger.warning("duplicate tool name %r ignored (first registration kept)", t.name)
                continue
            self._by_name[t.name] = t

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
        return ToolResult(content=json.dumps({"approval_id": raw, "vote": ballot.vote, "recorded": True}))

    return Tool(
        name="cast_approval_vote",
        description=(
            "Cast your approval vote on a gate you were notified about. Provide the "
            "approval_id from the notification, a boolean vote, and an optional rationale."
        ),
        input_schema=_CAST_APPROVAL_VOTE_SCHEMA,
        invoke=_invoke,
    )


_READ_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The skill's name, exactly as it appears in the skills index.",
        },
        "offset": {
            "type": "integer",
            "description": (
                "Resume reading at this character offset. Pass the truncated_at_offset "
                "returned by a previous call to read the next span of a long skill."
            ),
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}

# R31.15 / AC-33. A fixed per-call allowance, not "whatever is left of the window":
# `build_registry` runs before the request is assembled at all — and the request is
# rebuilt on the recompaction path — so any window measured here would be stale by
# construction. This does not fix the unbudgeted mid-tool-loop growth of FU-4 and does
# not claim to; it only keeps one skill body from being the thing that blows the window.
_SKILL_BODY_TOKEN_BUDGET = 8000


def _fit_skill_body(name: str, body: str, offset: int) -> tuple[str, int | None]:
    """The longest span of ``body[offset:]`` that fits, and where to resume after it.

    Two bounds, both non-decreasing in the span's length, so one binary search settles
    both: ``_SKILL_BODY_TOKEN_BUDGET`` against the estimate, and ``_MAX_TOOL_OUTPUT``
    against the *rendered* result. The second bound is not redundant with
    :func:`clip_tool_output` — it is what keeps the clip a no-op here. The clip cuts
    bytes, and a result severed mid-JSON would strand ``truncated_at_offset`` inside
    the string it was meant to index, which is exactly the contract AC-33 rests on.

    The offset is a **character** offset because it cannot be a token one:
    ``estimate_tokens`` is ``max(1, cjk + latin // 4)`` — non-additive, and with no
    inverse to seek by. Returns ``(span, next_offset)``; ``next_offset`` is None when
    the span reaches the end of the body.
    """
    rest = body[offset:]
    # Probed against the longest offset the payload could ever carry, so the real
    # result is never longer than what the search measured.
    envelope = len(_read_skill_payload(name, "", len(body)))

    def fits(span: str) -> bool:
        return (
            estimate_tokens(span) <= _SKILL_BODY_TOKEN_BUDGET
            and envelope + len(json.dumps(span, ensure_ascii=False)) <= _MAX_TOOL_OUTPUT
        )

    if fits(rest):
        return rest, None
    lo, hi = 0, len(rest)  # invariant: lo fits, hi does not
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(rest[:mid]):
            lo = mid
        else:
            hi = mid
    # Unreachable at these constants (one character always fits), but a zero-length
    # span would hand the model a continuation offset it has already read from, and it
    # would call forever.
    lo = max(lo, 1)
    return rest[:lo], offset + lo


def _read_skill_payload(name: str, span: str, next_offset: int | None) -> str:
    payload: dict[str, Any] = {"name": name, "body": span}
    if next_offset is not None:
        payload["truncated_at_offset"] = next_offset
    return json.dumps(payload, ensure_ascii=False)


def build_read_skill_tool(skills: Sequence[Skill]) -> Tool:
    """R31.15 — load a bound skill's body on demand from the turn's snapshot.

    ``skills`` is the snapshot ``resolve_bound_set`` already validated this turn, and
    the lookup is a dict over it. It must never re-query by name: the turn-time
    containment tap would then be decorative, and a name lookup against the `skills`
    table is an unscoped read primitive over every tenant's skills.
    """
    by_name = {s.name: s for s in skills}

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name", ""))
        skill = by_name.get(name)
        if skill is None:
            # An unknown name is a tool error, never a turn failure (R31.15): the model
            # can misread the index, and one bad call must not cost the user the turn.
            available = ", ".join(sorted(by_name)) or "(none)"
            return ToolResult(
                content=f"Unknown skill {name!r}. Bound skills: {available}.",
                is_error=True,
            )
        raw_offset = args.get("offset")
        offset = 0 if raw_offset is None else _opt_int(raw_offset)
        if offset is None:
            # Absent means "start at 0"; unparseable means the model sent something it
            # thinks is an offset and is wrong about. Coercing the second to 0 silently
            # restarts its continuation walk, and the two sibling error paths below and
            # above both say so out loud rather than guess.
            return ToolResult(content=f"offset must be an integer, got {raw_offset!r}.", is_error=True)
        if offset < 0 or offset > len(skill.body):
            return ToolResult(
                content=f"offset {offset} is outside skill {name!r} (0..{len(skill.body)}).",
                is_error=True,
            )
        span, next_offset = _fit_skill_body(skill.name, skill.body, offset)
        return ToolResult(content=clip_tool_output(_read_skill_payload(skill.name, span, next_offset)))

    return Tool(
        name="read_skill",
        description=(
            "Load the full instructions of one skill listed in the skills index, by name. "
            "Call it when a skill's description matches the task you are working on. Long "
            "bodies come back truncated with a truncated_at_offset; call again with that "
            "offset to read the next span."
        ),
        input_schema=_READ_SKILL_SCHEMA,
        invoke=_invoke,
    )


def build_registry(
    db: Any,
    *,
    agent_id: uuid.UUID,
    skills: Sequence[Skill],
    extra: list[Tool] | None = None,
) -> ToolRegistry:
    """Assemble the per-turn tool table for ``agent_id``.

    ``skills`` is the turn's validated bound-set snapshot and is **required**, not
    defaulted: a caller that forgets it is then a type error rather than an agent that
    silently loses ``read_skill``. Empty is the ordinary case and costs nothing — the
    tool is only offered when something is bound.
    """
    tools: list[Tool] = [build_update_wakeup_tool(db, agent_id=agent_id)]
    if skills:
        tools.append(build_read_skill_tool(skills))
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
    "BUILTIN_TOOL_NAMES",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_cast_approval_vote_tool",
    "build_read_skill_tool",
    "build_registry",
    "build_update_wakeup_tool",
    "clip_tool_output",
]
