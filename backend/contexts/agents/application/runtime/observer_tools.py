"""``present_observation`` — the one tool an observer-role turn gets ([R28.16]).

Separate from ``builtin_tools`` for the reasons ``activity_tools`` is: the
dependency on the activities context stays in one file, and this is not a tool any
``agent_tools`` row can produce. Its *source* is the binding's role, not a
configuration table.

WHAT MAKES THIS SAFE TO EXIST AT ALL
------------------------------------
The tool's only argument is an array of blocks validated against a schema built at
turn-assembly time, from a closed set of kinds fixed in platform code
(``observation_blocks``). Three things follow, and all three are structural rather
than remembered:

- **No number is model-supplied.** A computed block's branch declares no value
  property and closes with ``additionalProperties: false``, so a call carrying its
  own counts is rejected before ``invoke`` runs. The server fills them from
  room-scoped aggregates ([R28.17]).
- **No participant text can become an argument value that widens anything.** Every
  identifier argument is an ``enum`` of this room's project's reachable types
  ([R30.33]); there is no id argument a model could point at another room.
- **Nothing is rendered as markup.** The blocks reach the panel as data and are
  rendered as text nodes; the single markdown path is the existing
  ``renderMarkdown`` → DOMPurify pipeline.

A normal-role binding is never offered this tool, in any room, including one
holding an activity-control grant (AC-2). The role is the whole authorization.

TRANSACTION AND OUTPUT
----------------------
Read-only: the aggregates it calls issue ``SELECT``s on the turn's own session and
it commits nothing. Success writes the materialised array into a turn-scoped sink
that the observer branch drains after the stream, exactly as ``artifact_sink`` and
``activation_event_sink`` work. A turn that fails rolls back with the sink
discarded, so nothing is recorded for an observation that never happened.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ActivityType
from contexts.agents.application.runtime.activity_tools import type_enum_values
from contexts.agents.application.runtime.observation_blocks import (
    ATTEMPT_TABLE,
    FIELD_COVERAGE,
    MANDALA_GRID,
    MAX_BLOCKS,
    build_blocks_schema,
    materialise,
    oversize_violation,
    serialise_blocks,
    structural_violations,
)
from contexts.agents.application.runtime.tool_registry import Tool, ToolResult, clip_tool_output
from contexts.conversation.interfaces.facade import ConversationFacade

logger = logging.getLogger(__name__)

TOOL_NAME = "present_observation"

#: A mandala grid is three by three, so only a type declaring exactly nine
#: properties may be named by one. Filtering the enum rather than validating the
#: argument makes a mismatched grid unrepresentable instead of handled.
_MANDALA_PROPERTY_COUNT = 9


@dataclass(frozen=True, slots=True)
class ObservationPresentationContext:
    """Everything the tool needs, resolved once per turn.

    Constructed only by :func:`resolve_observation_presentation`, and only for an
    observer-role binding in a real room — so holding one of these *is* the
    authorization, the same shape ``ActivityControlContext`` has.

    ``types_by_key`` has already passed the reachability gate for the room's
    project, so a key in it is a type this room may legitimately be asked about.
    """

    chatroom_id: uuid.UUID
    project_id: uuid.UUID
    types_by_key: dict[str, ActivityType]


async def resolve_observation_presentation(
    db: AsyncSession, *, chatroom_id: uuid.UUID, is_observer: bool
) -> ObservationPresentationContext | None:
    """This turn's presentation context, or ``None``.

    **Fails closed on everything.** A normal-role binding, a headless turn with no
    room, an unresolvable project, or any exception at all yields ``None`` and
    therefore no tool. It logs before returning, because the caller
    (``TurnEngine._builtin_tools``) swallows every assembly exception into "no
    tools at all" — correct for its purpose, and it would otherwise make a bug in
    here silent.

    A room whose project has no reachable activity types still gets the tool: the
    narrative kinds do not depend on one, and the schema simply omits the computed
    branches. That is the opposite of ``resolve_activity_control``'s reading of an
    empty allowlist, and deliberately so — there, an empty enum left the model no
    legal call at all.
    """
    if not is_observer or chatroom_id is None:
        return None
    try:
        project_id = await ConversationFacade(db).project_id_for_chatroom(chatroom_id)
        if project_id is None:
            return None
        return ObservationPresentationContext(
            chatroom_id=chatroom_id,
            project_id=project_id,
            types_by_key=await _reachable_types(db, project_id=project_id),
        )
    except Exception:
        logger.warning(
            "observation presentation resolution failed for room %s; offering no tool",
            chatroom_id,
            exc_info=True,
        )
        return None


async def _reachable_types(db: AsyncSession, *, project_id: uuid.UUID) -> dict[str, ActivityType]:
    """``enum value -> type`` for every type this project may use ([R30.33]).

    Reuses ``activity_tools.type_enum_values`` rather than keying on ``key``
    directly: [R30.02] lets a project-owned type and an opted-in platform type
    share one key, and an ambiguous enum would let the model name a value that
    resolves to two different worksheets.
    """
    from contexts.activities.interfaces.facade import ActivitiesFacade

    types = await ActivitiesFacade(db).list_types(project_id)
    return type_enum_values(tuple(types))


def build_present_observation_tool(
    db: AsyncSession,
    *,
    presentation: ObservationPresentationContext,
    block_sink: list[dict[str, Any]] | None = None,
) -> Tool:
    """The tool for one observer turn ([R28.16]).

    ``block_sink`` mirrors ``artifact_sink``: the tool writes, the engine drains
    after the stream. Passing ``None`` builds a working tool that records nothing,
    which is only correct for a caller with no post-stream seam.
    """
    types_by_key = presentation.types_by_key
    coverage_keys = sorted(types_by_key)
    mandala_keys = sorted(
        key for key, at in types_by_key.items() if _declared_count(at) == _MANDALA_PROPERTY_COUNT
    )
    schema = build_blocks_schema(
        coverage_keys=coverage_keys,
        mandala_keys=mandala_keys,
        table_keys=coverage_keys,
    )

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        def refuse(reasons: list[str]) -> ToolResult:
            # A refusal leaves the sink exactly as it was, which is not the same
            # sentence in both cases: after a successful earlier call, "nothing was
            # recorded" would tell the model its blocks are gone, it would fall back
            # to plain prose, and the engine would then record the superseded
            # layout and discard that prose entirely.
            return ToolResult(content=_refusal(reasons, kept=bool(block_sink)), is_error=True)

        blocks = list(args.get("blocks") or [])
        violations = structural_violations(blocks)
        if violations:
            return refuse(violations)

        filled, refusals = await materialise(
            db, chatroom_id=presentation.chatroom_id, blocks=blocks, types_by_key=types_by_key
        )
        if refusals:
            return refuse(refusals)
        oversize = oversize_violation(filled)
        if oversize:
            return refuse([oversize])
        if not serialise_blocks(filled).strip():
            # The engine records these blocks with their serialisation as
            # `content_md`, and an observer turn may carry no prose at all — so a
            # block array that renders to nothing would persist an empty
            # observation, which is the one thing the empty-turn guard exists to
            # prevent. The schema cannot catch it: `minLength` accepts whitespace
            # and `schema_violations` strips `pattern` outright.
            return refuse(["these blocks render to nothing at all"])

        if block_sink is not None:
            # Last call wins: the sink is replaced, not appended to, so a model
            # that revises its layout does not end up with both versions stacked.
            block_sink.clear()
            block_sink.extend(filled)
        computed = sum(1 for b in filled if b.get("kind") in _COMPUTED)
        return ToolResult(
            content=clip_tool_output(
                f"Recorded {len(filled)} block(s) for this observation"
                + (f", {computed} of them filled in from room data" if computed else "")
                + ". Calling this again replaces them; the last call is what the teacher sees."
            )
        )

    return Tool(
        name=TOOL_NAME,
        description=_description(coverage_keys, mandala_keys),
        input_schema=schema,
        invoke=_invoke,
    )


_COMPUTED = frozenset({FIELD_COVERAGE, MANDALA_GRID, ATTEMPT_TABLE})


def _declared_count(activity_type: ActivityType) -> int:
    properties = activity_type.payload_schema.get("properties")
    return len(properties) if isinstance(properties, dict) else 0


def _refusal(reasons: list[str], *, kept: bool) -> str:
    """A refusal the model can act on: what this call did, and why.

    ``kept`` says an earlier call in this turn already succeeded and its blocks
    still stand. Saying "nothing was recorded" then would be false in the
    direction that costs the most: the model would take its analysis as lost, fall
    back to prose, and the engine would record the superseded layout while
    discarding that prose.
    """
    head = (
        "This call was not recorded; the blocks from your previous call still stand."
        if kept
        else "Nothing was recorded."
    )
    return clip_tool_output(f"{head} " + " ".join(reasons) + " Fix these and call present_observation again.")


def _description(coverage_keys: list[str], mandala_keys: list[str]) -> str:
    lines = [
        "Deliver this observation as an ordered list of presentation blocks the teacher "
        "will read. You choose which blocks, in what order, and you write their titles "
        f"and text. At most {MAX_BLOCKS} blocks.",
        "prose, key_points and timeline carry text you write.",
    ]
    if coverage_keys:
        lines.append(
            "field_coverage, mandala_grid and attempt_table are filled in by the platform "
            "from this room's own records: you choose which activity and how to frame it, "
            "and the numbers are measured, not yours. Do not restate them as scores."
        )
        lines.append("Activities you may point a figure at: " + ", ".join(coverage_keys) + ".")
        if mandala_keys:
            lines.append("Of those, nine-cell grids: " + ", ".join(mandala_keys) + ".")
    lines.append(
        "Every block except prose carries a platform-written basis line saying what it "
        "rests on. Not calling this tool is fine: your reply is then recorded as it stands."
    )
    return " ".join(lines)


__all__ = [
    "TOOL_NAME",
    "ObservationPresentationContext",
    "build_present_observation_tool",
    "resolve_observation_presentation",
]
