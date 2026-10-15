"""Who is in the room, as the room's agents are told ([R13.33], [R13.74]).

Two outputs share one identity rule. Every human transcript row is prefixed with
the server-derived participant code -- the same ``u:`` code the activity and draft
context use -- followed by the display label when there is one; and a bounded
facts block lists the people that code space covers on this turn. Pure functions:
the turn engine resolves labels, presence and history, and this module only
decides what the model reads.

The code sits in square brackets because ``one_line_label`` strips brackets from
every label: a participant cannot name themselves into another person's code.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from contexts.activities.domain.subject_code import subject_code
from contexts.conversation.interfaces.facade import RoomPresenceSnapshot
from shared_kernel.tokens import estimate_tokens

#: [R13.74] bounds on the supplemental rows and on the block as a whole.
MAX_PARTICIPANT_ROWS = 100
MAX_PARTICIPANT_TOKENS = 2000

PARTICIPANTS_HEADER = "[Participants]"


def code_prefix(user_id: uuid.UUID) -> str:
    return f"[{subject_code(user_id)}]"


def human_prefix(user_id: uuid.UUID, label: str | None) -> str:
    """``[u:xxxxxxxx] Label`` or ``[u:xxxxxxxx]``; ``label`` is already one-lined and marked."""
    return f"{code_prefix(user_id)} {label}" if label else code_prefix(user_id)


#: Exact, because every code has the same length: what the code adds to each
#: human row, for the compaction decision made before any label is resolved.
CODE_PREFIX_TOKENS = estimate_tokens(f"{code_prefix(uuid.UUID(int=0))} : ")


def ambiguous_ids(user_ids: Collection[uuid.UUID]) -> frozenset[uuid.UUID]:
    """Ids whose code another id in the same set shares."""
    by_code: dict[str, list[uuid.UUID]] = {}
    for uid in user_ids:
        by_code.setdefault(subject_code(uid), []).append(uid)
    return frozenset(uid for ids in by_code.values() if len(ids) > 1 for uid in ids)


@dataclass(frozen=True, slots=True)
class TranscriptSpeaker:
    user_id: uuid.UUID
    last_message_at: datetime | None


def transcript_speakers(rows: Sequence[tuple[uuid.UUID, datetime | None]]) -> list[TranscriptSpeaker]:
    """Distinct human senders of the included rows, most recent first, then by id."""
    latest: dict[uuid.UUID, datetime | None] = {}
    for uid, at in rows:
        prev = latest.get(uid)
        if uid not in latest or (at is not None and (prev is None or at > prev)):
            latest[uid] = at
    by_id = sorted(latest.items(), key=lambda item: str(item[0]))
    dated = sorted(((uid, at) for uid, at in by_id if at is not None), key=lambda item: item[1], reverse=True)
    return [TranscriptSpeaker(uid, at) for uid, at in dated] + [
        TranscriptSpeaker(uid, None) for uid, at in by_id if at is None
    ]


def _clock(at: datetime) -> str:
    return at.strftime("%Y-%m-%d %H:%M UTC")


def render_participant_facts(
    *,
    speakers: Sequence[TranscriptSpeaker],
    presence: RoomPresenceSnapshot,
    labels: Mapping[uuid.UUID, str],
    creator_id: uuid.UUID | None,
    window_size: int,
    window_start: datetime | None,
    window_end: datetime | None,
) -> str:
    """The [R13.74] block. ``labels`` maps a user id to its marked display label;
    an id without one is listed by code alone."""
    speaking = {s.user_id for s in speakers}
    connected = set(presence.user_ids)
    ordered = [s.user_id for s in speakers] + sorted(connected - speaking, key=str)
    ambiguous = ambiguous_ids(ordered)

    window = f"the {window_size} transcript messages you can see"
    if window_start is not None and window_end is not None:
        window += f" (sent {_clock(window_start)} to {_clock(window_end)})"
    lines = [
        f"{PARTICIPANTS_HEADER} Snapshot at {_clock(presence.observed_at)}. Listed: people with a message "
        f"among {window}, then other people connected to the room now. Nobody else in the room is "
        "listed, and this is not an attendance list.",
        '"connected" means a live connection to the room, not attention. "no message in this window" '
        "says nothing about earlier messages. Codes match the transcript prefixes and the activity and "
        "draft context.",
    ]
    if not presence.available:
        lines.append('Connection state could not be read this turn: "unknown" means not known, not absent.')
    if ambiguous:
        lines.append(
            'Rows marked "ambiguous code" share a code with another row: they are different people, and '
            "neither can be told apart by code alone."
        )

    footer: list[str] = []
    rows: list[str] = []
    used = estimate_tokens("\n".join(lines))
    omitted = 0
    for uid in ordered:
        if len(rows) >= MAX_PARTICIPANT_ROWS:
            omitted += 1
            continue
        if not presence.available:
            state = "connection unknown"
        else:
            state = "connected" if uid in connected else "not connected"
        facts = [state, "message in this window" if uid in speaking else "no message in this window"]
        if uid == creator_id:
            facts.insert(0, "room creator")
        if uid in ambiguous:
            facts.append("ambiguous code")
        row = f"- {human_prefix(uid, labels.get(uid))}: {'; '.join(facts)}"
        cost = estimate_tokens(row) + 1
        # Room for the omission line, whatever it ends up saying.
        if used + cost > MAX_PARTICIPANT_TOKENS - 30:
            omitted += 1
            continue
        rows.append(row)
        used += cost
    if omitted:
        footer.append(f"{omitted} more people are not listed (row or size limit).")
    if presence.truncated:
        footer.append("More people are connected than the connection read returned; not all are listed.")
    if not ordered:
        rows.append("- nobody to list")
    return "\n".join(lines + rows + footer)


#: The measure pass's worst case. CJK filler because `estimate_tokens` charges one
#: token per CJK character, so this is exactly the cap in the unit the budget spends.
PARTICIPANT_FACTS_MEASURE = "王" * MAX_PARTICIPANT_TOKENS


__all__ = [
    "CODE_PREFIX_TOKENS",
    "MAX_PARTICIPANT_ROWS",
    "MAX_PARTICIPANT_TOKENS",
    "PARTICIPANTS_HEADER",
    "PARTICIPANT_FACTS_MEASURE",
    "TranscriptSpeaker",
    "ambiguous_ids",
    "code_prefix",
    "human_prefix",
    "render_participant_facts",
    "transcript_speakers",
]
