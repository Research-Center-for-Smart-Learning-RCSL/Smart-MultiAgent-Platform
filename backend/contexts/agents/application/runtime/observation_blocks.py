"""The closed set of presentation blocks an observer may assemble ([R28.15]-[R28.19]).

An observation is an ordered array of blocks drawn from a set fixed in this file.
The agent chooses which blocks, in what order, with what titles and free text; it
never writes code, markup, or a number behind a figure.

THE SPLIT THAT MAKES THIS SAFE TO GRANT
---------------------------------------
Narrative kinds (``prose``, ``key_points``, ``timeline``) carry text the agent
wrote. Computed kinds (``field_coverage``, ``mandala_grid``, ``attempt_table``)
carry values the **server** measured at tool-invoke time, and their schemas accept
no value at all: the agent supplies only a selection and a framing. A participant
can therefore persuade an agent to *include* a coverage figure and cannot change a
number in one, because the model is never asked for one. A tool call that supplies
its own counts is rejected as an unknown property, not silently trusted
([R28.17]).

BASIS LABELS ARE PLATFORM-AUTHORED
----------------------------------
Every non-prose block renders a sentence saying what it rests on and what it
cannot mean. The agent picks *which* of three applies; it does not write one, and
no tool argument suppresses it. Computed blocks are not even offered the choice —
the server stamps ``server_facts`` on them, so a computed block cannot be
mislabelled by its caller ([R28.19]).

SERIALISATION
-------------
:func:`serialise_blocks` renders the array to markdown, which is stored in
``content_md`` on the same insert. That is what release-to-room ([R28.06]), the
release dialog's plain-text override and the observer's own memory window
([R28.05]) read, so it must be deterministic. It emits **English**: it runs on a
turn, with no request locale. The rendered UI uses ``$t()`` and is the localised
surface.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from contexts.activities.domain.models import (
    ActivityType,
    AttemptSummary,
    FieldCoverage,
    MandalaGrid,
)
from contexts.activities.interfaces.facade import ActivitiesFacade

logger = logging.getLogger(__name__)

PROSE = "prose"
KEY_POINTS = "key_points"
TIMELINE = "timeline"
FIELD_COVERAGE = "field_coverage"
MANDALA_GRID = "mandala_grid"
ATTEMPT_TABLE = "attempt_table"

NARRATIVE_KINDS = (PROSE, KEY_POINTS, TIMELINE)
COMPUTED_KINDS = (FIELD_COVERAGE, MANDALA_GRID, ATTEMPT_TABLE)
ALL_KINDS = NARRATIVE_KINDS + COMPUTED_KINDS

SERVER_FACTS = "server_facts"
RECENT_WINDOW = "recent_window"
TRANSCRIPT = "transcript"
BASIS_VALUES = (SERVER_FACTS, RECENT_WINDOW, TRANSCRIPT)

#: What each basis renders as in the stored markdown. The UI renders the same
#: three claims through ``conversation.observers.basis.*``; if one side is
#: reworded the other must move with it, because a released observation carries
#: this text into the room while the panel shows the translated one ([R28.19]).
BASIS_SENTENCES: dict[str, str] = {
    SERVER_FACTS: (
        "Basis: computed by the server over this room's submissions. It counts "
        "submissions, not participants, and says nothing about who did not submit."
    ),
    RECENT_WINDOW: (
        "Basis: read off the recent-activity window, which holds only the most recent "
        "events and is not a complete record of the room."
    ),
    TRANSCRIPT: (
        "Basis: read off what was said in the room. A description of the discussion, not a measurement."
    ),
}

MAX_BLOCKS = 12
MAX_POINTS = 8
MAX_TIMELINE_ENTRIES = 12
MAX_TABLE_ROWS = 30
DEFAULT_TABLE_ROWS = 20

#: Ceiling on the stored array, measured on the **materialised** JSON — the bytes
#: that actually reach the column and the panel's 50-row page. The per-string caps
#: alone do not bound it: twelve maximum-length prose blocks are over twice this.
MAX_BLOCKS_BYTES = 20 * 1024


def build_blocks_schema(
    *,
    coverage_keys: list[str],
    mandala_keys: list[str],
    table_keys: list[str],
) -> dict[str, Any]:
    """The tool's ``input_schema``, with this turn's type enums baked in.

    A computed kind is **omitted entirely** when its enum would be empty rather
    than offered over no legal value: a tool the model may call and then find no
    argument for is worse than one it was never offered, which is the same reading
    ``resolve_activity_control`` takes of an empty allowlist.

    ``mandala_keys`` is already filtered to types declaring exactly nine
    properties, so a mismatched grid is unrepresentable rather than handled.

    Every branch closes with ``additionalProperties: false``. That is what makes
    "the model cannot state a number" structural: a computed branch declares no
    value property, so a call carrying one is rejected by
    :func:`~contexts.agents.application.runtime.tool_registry.schema_violations`
    before ``invoke`` ever runs (AC-6).
    """
    branches: list[dict[str, Any]] = [
        _prose_branch(),
        _key_points_branch(),
        _timeline_branch(),
    ]
    if coverage_keys:
        branches.append(_coverage_branch(FIELD_COVERAGE, coverage_keys))
    if mandala_keys:
        branches.append(_coverage_branch(MANDALA_GRID, mandala_keys))
    if table_keys:
        branches.append(_attempt_table_branch(table_keys))

    offered = [str(branch["properties"]["kind"]["enum"][0]) for branch in branches]
    return {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_BLOCKS,
                "description": (
                    "The observation, in order. Each entry is one block; the reader sees them top to bottom."
                ),
                "items": {
                    "type": "object",
                    "required": ["kind"],
                    # Repeated outside `oneOf` purely for the error message: an
                    # unknown kind otherwise comes back only as "not valid under any
                    # of the given schemas", which tells the model nothing it can
                    # act on. The branches still do the real work.
                    "properties": {"kind": {"type": "string", "enum": offered}},
                    "oneOf": branches,
                },
            }
        },
        "required": ["blocks"],
        "additionalProperties": False,
    }


def _basis_property() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(BASIS_VALUES),
        "description": (
            "What this block rests on. The platform renders a fixed sentence for each; "
            "you choose which applies, you do not write it. "
            "server_facts = counted by the server over this room's submissions. "
            "recent_window = read off the recent-activity window, which is incomplete. "
            "transcript = read off what was said in the room, which is not a measurement."
        ),
    }


def _title_property() -> dict[str, Any]:
    return {"type": "string", "maxLength": 120, "description": "Optional heading for this block."}


def _caveat_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 280,
        "description": "One optional sentence on what this block does not show.",
    }


def _prose_branch() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["kind", "text"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": [PROSE]},
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "description": "Markdown paragraph text. May appear at any position in the array.",
            },
        },
    }


def _key_points_branch() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["kind", "basis", "points"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": [KEY_POINTS]},
            "title": _title_property(),
            "basis": _basis_property(),
            "caveat": _caveat_property(),
            "points": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_POINTS,
                "items": {
                    "type": "object",
                    "required": ["text"],
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 400},
                        "evidence": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "The observation this point rests on.",
                        },
                    },
                },
            },
            "next_step": {
                "type": "string",
                "maxLength": 400,
                "description": "One optional suggested next step, shown after the points.",
            },
        },
    }


def _timeline_branch() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["kind", "basis", "entries"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": [TIMELINE]},
            "title": _title_property(),
            "basis": _basis_property(),
            "caveat": _caveat_property(),
            "entries": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_TIMELINE_ENTRIES,
                "items": {
                    "type": "object",
                    "required": ["label"],
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "minLength": 1, "maxLength": 120},
                        "detail": {"type": "string", "maxLength": 400},
                    },
                },
            },
        },
    }


def _coverage_branch(kind: str, keys: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["kind", "type_key"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": [kind]},
            "title": _title_property(),
            "caveat": _caveat_property(),
            "type_key": {
                "type": "string",
                "enum": keys,
                "description": "Which activity's answers to count. Must be one of the listed values.",
            },
        },
    }


def _attempt_table_branch(keys: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["kind"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": [ATTEMPT_TABLE]},
            "title": _title_property(),
            "caveat": _caveat_property(),
            "type_key": {
                "type": "string",
                "enum": keys,
                "description": "Narrow to one activity. Omit to cover every activity in this room.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TABLE_ROWS,
                "description": f"How many rows at most (default {DEFAULT_TABLE_ROWS}).",
            },
        },
    }


def structural_violations(blocks: list[dict[str, Any]]) -> list[str]:
    """The rules the JSON Schema cannot state, as messages the model can act on.

    Just the duplicate-computed-block rule today. The count and the string lengths
    are in the schema, where a provider can also enforce them during decoding; the
    byte ceiling is checked after materialisation, on the bytes that are actually
    stored.
    """
    seen: set[tuple[str, str | None]] = set()
    violations: list[str] = []
    for block in blocks:
        kind = str(block.get("kind", ""))
        if kind not in COMPUTED_KINDS:
            continue
        key = (kind, block.get("type_key"))
        if key in seen:
            target = key[1] or "every activity"
            violations.append(
                f"two {kind} blocks for {target}; one figure per activity per kind, "
                "reorder or drop the duplicate"
            )
        seen.add(key)
    return violations


async def materialise(
    db: Any,
    *,
    chatroom_id: uuid.UUID,
    blocks: list[dict[str, Any]],
    types_by_key: dict[str, ActivityType],
) -> tuple[list[dict[str, Any]], list[str]]:
    """``(blocks with every computed value filled in, refusals)``.

    Narrative blocks pass through unchanged. Each computed block is replaced by
    itself plus the aggregate's own fields and a server-stamped ``basis``.

    An aggregate that returns ``None`` becomes a **refusal**, not an empty figure:
    the room may have adopted the coverage validator only part-way through, in
    which case a chart of zeroes would assert that nobody answered anything. The
    caller reports the refusals to the model, which can drop the block and call
    again.
    """
    facade = ActivitiesFacade(db)
    out: list[dict[str, Any]] = []
    refusals: list[str] = []
    for block in blocks:
        kind = str(block.get("kind", ""))
        if kind not in COMPUTED_KINDS:
            out.append(dict(block))
            continue
        key = block.get("type_key")
        activity_type = types_by_key.get(str(key)) if key is not None else None
        if key is not None and activity_type is None:
            # Unreachable through a schema-validating registry, and kept anyway:
            # this is the boundary that must hold even if validation is loosened.
            refusals.append(f"{kind}: {key!r} is not an activity in this room")
            continue
        filled = await _computed_fields(
            facade,
            kind=kind,
            chatroom_id=chatroom_id,
            activity_type=activity_type,
            limit=int(block.get("limit") or DEFAULT_TABLE_ROWS),
        )
        if filled is None:
            refusals.append(_no_data(kind, key))
            continue
        out.append({**block, **filled, "basis": SERVER_FACTS})
    return out, refusals


def _no_data(kind: str, key: Any) -> str:
    where = f"for {key!r}" if key else "in this room"
    if kind == ATTEMPT_TABLE:
        return f"{kind}: no submissions {where} yet, so the table would be empty. Drop this block."
    if kind == MANDALA_GRID:
        return (
            f"{kind}: {key!r} either has no per-field record of what was answered or is not a "
            "nine-cell activity, so no grid can be drawn. Drop this block; describe what you "
            "saw instead."
        )
    return (
        f"{kind}: no submission {where} records which fields were answered, so there is "
        "nothing to count. Drop this block; describe what you saw instead."
    )


async def _computed_fields(
    facade: ActivitiesFacade,
    *,
    kind: str,
    chatroom_id: uuid.UUID,
    activity_type: ActivityType | None,
    limit: int,
) -> dict[str, Any] | None:
    if kind == ATTEMPT_TABLE:
        summary = await facade.attempt_summary(
            chatroom_id=chatroom_id, activity_type=activity_type, limit=limit
        )
        return _summary_fields(summary) if summary else None
    if activity_type is None:
        # Unreachable: `type_key` is required for both coverage kinds and its enum
        # is built from the same mapping. Treated as "no data" rather than raising,
        # because the fail-closed direction here is a refused block.
        return None
    if kind == MANDALA_GRID:
        grid = await facade.mandala_grid(chatroom_id=chatroom_id, activity_type=activity_type)
        return _grid_fields(grid) if grid else None
    coverage = await facade.field_coverage(chatroom_id=chatroom_id, activity_type=activity_type)
    return _coverage_fields(coverage) if coverage else None


def _coverage_fields(coverage: FieldCoverage) -> dict[str, Any]:
    return {
        "type_name": coverage.type_name,
        "submissions_counted": coverage.submissions_counted,
        "cells": [{"name": c.name, "title": c.title, "filled": c.filled} for c in coverage.cells],
    }


def _grid_fields(grid: MandalaGrid) -> dict[str, Any]:
    return {
        "type_name": grid.type_name,
        "submissions_counted": grid.submissions_counted,
        "rows": [[{"name": c.name, "title": c.title, "filled": c.filled} for c in row] for row in grid.rows],
    }


def _summary_fields(summary: AttemptSummary) -> dict[str, Any]:
    return {
        "type_name": summary.type_name,
        "submissions_counted": summary.submissions_counted,
        "truncated": summary.truncated,
        "rows": [
            {
                "subject_code": r.subject_code,
                "attempts": r.attempts,
                "submissions": r.submissions,
                "latest_outcome": r.latest_outcome,
                "latest_error_class": r.latest_error_class,
            }
            for r in summary.rows
        ],
    }


def oversize_violation(blocks: list[dict[str, Any]]) -> str | None:
    """The stored-bytes ceiling, or ``None``. Measured on what actually persists."""
    size = len(json.dumps(blocks, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size <= MAX_BLOCKS_BYTES:
        return None
    return (
        f"the blocks serialise to {size} bytes, over the {MAX_BLOCKS_BYTES} limit; "
        "shorten the text or use fewer blocks"
    )


# --------------------------------------------------------------------------- #
# Markdown serialisation ([R28.19] — the basis travels with a released block)
# --------------------------------------------------------------------------- #


def serialise_blocks(blocks: list[dict[str, Any]]) -> str:
    """The blocks as markdown, deterministically.

    Stored in ``content_md`` rather than rendered on read, so an observation stays
    readable if the block schema is ever rolled back and so a released message
    never depends on a serialiser version that has moved under it.

    The ``caveat`` and the basis sentence are always emitted, which is what makes
    a released observation carry its own limits into the room with it.
    """
    parts = [part for block in blocks if (part := _serialise_one(block))]
    return "\n\n".join(parts)


def _serialise_one(block: dict[str, Any]) -> str:
    kind = str(block.get("kind", ""))
    if kind == PROSE:
        return str(block.get("text", "")).strip()
    body = {
        KEY_POINTS: _serialise_key_points,
        TIMELINE: _serialise_timeline,
        FIELD_COVERAGE: _serialise_coverage,
        MANDALA_GRID: _serialise_grid,
        ATTEMPT_TABLE: _serialise_table,
    }.get(kind)
    if body is None:
        # A kind this serialiser does not know cannot be rendered honestly, and
        # inventing a rendering for it would put unlabelled text in the room on
        # release. Dropping it is the safe direction; the stored array keeps it.
        logger.warning("no serialiser for observation block kind %r; omitting it", kind)
        return ""
    lines = _heading(block) + body(block) + _footnotes(block)
    return "\n".join(line for line in lines if line)


def _heading(block: dict[str, Any]) -> list[str]:
    title = str(block.get("title") or "").strip()
    return [f"### {_inline(title)}", ""] if title else []


def _footnotes(block: dict[str, Any]) -> list[str]:
    out = [""]
    caveat = str(block.get("caveat") or "").strip()
    if caveat:
        out.append(_inline(caveat))
    sentence = BASIS_SENTENCES.get(str(block.get("basis", "")))
    if sentence:
        out.append(f"_{sentence}_")
    return out if len(out) > 1 else []


def _serialise_key_points(block: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for point in block.get("points") or []:
        text = _inline(str(point.get("text", "")))
        evidence = _inline(str(point.get("evidence") or ""))
        lines.append(f"- {text} ({evidence})" if evidence else f"- {text}")
    next_step = str(block.get("next_step") or "").strip()
    if next_step:
        lines.extend(["", f"Suggested next step: {_inline(next_step)}"])
    return lines


def _serialise_timeline(block: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for entry in block.get("entries") or []:
        label = _inline(str(entry.get("label", "")))
        detail = _inline(str(entry.get("detail") or ""))
        lines.append(f"- {label}: {detail}" if detail else f"- {label}")
    return lines


def _serialise_coverage(block: dict[str, Any]) -> list[str]:
    rows = [
        f"| {_cell(c.get('title') or c.get('name'))} | {int(c.get('filled', 0))} |"
        for c in block.get("cells") or []
    ]
    return ["| Field | Answered |", "| --- | --- |", *rows, "", _counted(block)]


def _serialise_grid(block: dict[str, Any]) -> list[str]:
    # An empty header row: markdown tables require one, and the grid's columns
    # have no names — position is the whole meaning. The block's own title says
    # what the figure is.
    lines = ["|  |  |  |", "| --- | --- | --- |"]
    for row in block.get("rows") or []:
        cells = [f"{_cell(c.get('title') or c.get('name'))} ({int(c.get('filled', 0))})" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, "", _counted(block)]


def _serialise_table(block: dict[str, Any]) -> list[str]:
    rows = []
    for r in block.get("rows") or []:
        outcome = str(r.get("latest_outcome", ""))
        error_class = r.get("latest_error_class")
        if error_class:
            outcome = f"{outcome} [{_cell(error_class)}]"
        rows.append(
            f"| {_cell(r.get('subject_code'))} | {int(r.get('attempts', 0))} "
            f"| {int(r.get('submissions', 0))} | {_cell(outcome)} |"
        )
    tail = [_counted(block)]
    if block.get("truncated"):
        tail.append(f"Showing the {len(rows)} most recent; this room has more.")
    return [
        "| Participant | Attempts | Submissions | Latest |",
        "| --- | --- | --- | --- |",
        *rows,
        "",
        *tail,
    ]


def _counted(block: dict[str, Any]) -> str:
    """The denominator, in the wording [R28.18] requires: submissions, not people."""
    n = int(block.get("submissions_counted", 0))
    return f"{n} submission{'' if n == 1 else 's'} counted."


def _inline(text: str) -> str:
    """Collapse a value to one line.

    A block is a markdown fragment inside a document the platform assembles, and
    an agent-authored string carrying a newline could otherwise open a heading or
    a list item of its own outside the block it belongs to. Not an XSS control —
    the rendered path is DOMPurify either way — but a released observation must
    keep the shape the panel showed the creator.
    """
    return " ".join(text.split())


def _cell(value: Any) -> str:
    """A table cell: one line, with the column separator escaped.

    Field titles are owner-authored and a ``|`` in one would otherwise split the
    row into extra columns and silently shift every value after it.
    """
    return _inline(str(value if value is not None else "")).replace("|", "\\|")


__all__ = [
    "ALL_KINDS",
    "ATTEMPT_TABLE",
    "BASIS_SENTENCES",
    "BASIS_VALUES",
    "COMPUTED_KINDS",
    "DEFAULT_TABLE_ROWS",
    "FIELD_COVERAGE",
    "KEY_POINTS",
    "MANDALA_GRID",
    "MAX_BLOCKS",
    "MAX_BLOCKS_BYTES",
    "MAX_TABLE_ROWS",
    "NARRATIVE_KINDS",
    "PROSE",
    "SERVER_FACTS",
    "TIMELINE",
    "build_blocks_schema",
    "materialise",
    "oversize_violation",
    "serialise_blocks",
    "structural_violations",
]
