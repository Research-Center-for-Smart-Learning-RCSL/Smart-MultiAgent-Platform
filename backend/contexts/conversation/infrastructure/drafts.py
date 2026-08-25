"""Unsent composer and worksheet text, held in Redis under a TTL (§32, [R32.02]).

A draft is text its author has **not chosen to send**. That single fact decides the
whole shape of this module:

  - `ws:draft:{room_id}:{user_id}:{surface}[:{key}]` -> JSON `{content, updated_at}`
  - `ws:draft:rooms:{room_id}`                       -> SET of that room's live
                                                        draft keys, so a read is one
                                                        `SMEMBERS` + one `MGET`
                                                        rather than a `SCAN` over a
                                                        shared keyspace

**Redis and never PostgreSQL.** Durability is the wrong property for unshared text:
a column would put it in backups, exports, retention scans and every operator's
`SELECT *`, and each of those is a place a half-typed sentence about a distressing
event must not be. Presence takes the same posture for a weaker reason
(`presence.py`'s docstring); here it is the requirement.

**Not under `ws:presence:`.** `scrub_stale_presence` scans that prefix and tells a
roster key from a conns key by counting `:`, so a fourth shape underneath it would be
misread by a sweep that predates this module. `_typing_key`'s comment records the same
trap; this is the second key to step around it.

**No policy lives here.** The store answers "what is in this room", not "who may see
it": the grant is checked before a write reaches `put` and the per-type consent gates
are applied by the tool at read time ([R32.04]). `presence.py` mixes key layout with
policy and is the example this file follows in idiom and not in scope.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from redis.asyncio import Redis

from shared_kernel.auth.clients import as_utc, get_redis, now

logger = logging.getLogger(__name__)

COMPOSER: Final = "composer"
ACTIVITY: Final = "activity"
SURFACES: Final = frozenset({COMPOSER, ACTIVITY})

# Deliberately NOT `_CONN_TTL_SECONDS`. That constant is the horizon at which a
# *connection* stops being believed, and the reasoning behind it (`presence.py:33-53`)
# does not transfer: a worksheet legitimately sits untouched for ten minutes while a
# student thinks, and retracting it on the connection horizon would delete the draft
# most worth reading. Fifteen minutes is long enough to survive thinking and short
# enough that a closed tab stops being readable within one lesson segment.
DRAFT_TTL_SECONDS: Final = 900

# The room index outlives any single entry by one TTL, so a key that expires between
# an `SMEMBERS` and its `MGET` leaves a member behind rather than an unreadable set.
# `list_for_room` reconciles that member away; see its docstring.
_INDEX_TTL_SECONDS: Final = DRAFT_TTL_SECONDS * 2

# Per-surface and per-user caps. A draft is reported on a timer, so an unbounded one
# is a slow memory leak that a single participant can drive on their own, and the
# tool's own `clip_tool_output` bounds only what one call renders, not what is stored.
MAX_CONTENT_CHARS: Final = 4_000
MAX_USER_CHARS: Final = 16_000

# The byte budget above bounds how much one participant can store; it does **not**
# bound how many keys they can create, because a thousand one-character drafts fit
# inside it comfortably. Each distinct activity key is its own entry and its own
# index member, so without a count cap a hostile client can mint entries at the
# throttle rate for a whole TTL window (~450 per connection, times the per-user
# connection cap) and make `list_for_room`'s MGET enormous on every agent turn.
#
# Two is the honest working number -- a room runs one activity at a time, so a
# participant has a composer draft and one worksheet. Eight leaves room for stale
# entries from earlier rounds that the client's own clears missed.
MAX_USER_ENTRIES: Final = 8

#: Appended to a clipped draft. The agent has to be able to tell "they stopped there"
#: from "we stopped showing it" -- the first is a fact about the participant and the
#: second is a fact about this module, and an agent that confuses them reports a
#: student as having written less than they did.
TRUNCATION_MARKER: Final = "\n[truncated]"

# An activity key comes from the client. It is only ever a *key component*, never a
# pattern or an argument, but a value carrying ':' would silently move an entry into
# another surface's key shape, and one carrying a newline or a wildcard would make the
# stored key unmatchable by its own owner. Bounded and sanitised on the way in.
_MAX_KEY_CHARS: Final = 128


@dataclass(frozen=True, slots=True)
class DraftEntry:
    """One participant's unsent text on one surface, with how old it is.

    ``age_seconds`` is the load-bearing field, not a convenience: a draft survives a
    disconnect for up to its TTL ([R32.02]), so an agent reading one cannot otherwise
    tell live typing from someone who closed the tab twelve minutes ago. OQ-1 of the
    dossier records that reporting the age is the chosen answer to that.

    ``truncated`` says the stored value was clipped, so the reader can say so rather
    than reporting a student as having written less than they did.
    """

    user_id: uuid.UUID
    surface: str
    key: str | None
    content: str
    age_seconds: int
    truncated: bool


def _entry_key(room_id: uuid.UUID, user_id: uuid.UUID, surface: str, key: str | None) -> str:
    base = f"ws:draft:{room_id}:{user_id}:{surface}"
    return f"{base}:{key}" if key else base


def _index_key(room_id: uuid.UUID) -> str:
    """The room's set of live draft keys.

    Shaped after `presence.py`'s `_room_key` (a per-*room* set), **not** after
    `_user_rooms_key` (a per-*user* reverse index). The question this index answers is
    "what is being typed in this room", which is a room-scoped read; a per-user index
    would make it one round trip per participant.
    """
    return f"ws:draft:rooms:{room_id}"


def normalise_key(raw: Any) -> str | None:
    """An activity key safe to place in a Redis key, or ``None``.

    Client-supplied. Returning ``None`` for anything unusable rather than raising: a
    malformed frame costs that draft, never the socket, and the caller treats a
    missing key on the activity surface as a frame to drop.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or len(text) > _MAX_KEY_CHARS:
        return None
    # ':' would move the entry into a different key shape; whitespace and '*'/'?' would
    # make it unmatchable or turn a later `SMEMBERS` value into something a careless
    # pattern read could widen. None of these appear in a real `ActivityType.key`.
    if any(ch in text for ch in (":", "*", "?", "[", "]", "\n", "\r", "\t", " ")):
        return None
    return text


def clip(content: str) -> tuple[str, bool]:
    """``(stored_value, was_truncated)`` for one surface's content.

    Truncated with a marker rather than rejected: a participant who writes past the
    cap has written something, and silently storing nothing would make a long answer
    indistinguishable from an empty one.
    """
    if len(content) <= MAX_CONTENT_CHARS:
        return content, False
    return content[:MAX_CONTENT_CHARS] + TRUNCATION_MARKER, True


class DraftStore:
    """Room-scoped live drafts. Every method is best-effort and total.

    A Redis fault costs a draft update or a read, never a WebSocket connection or an
    agent's turn: this is fire-and-forget state whose worst failure is an agent
    reading slightly older text. Errors are logged and swallowed here rather than at
    each of the four call sites, so the posture cannot drift between them.
    """

    def __init__(self, redis: Redis[str] | None = None) -> None:
        # Injectable for the unit tier; production resolves the shared client, the
        # same shape `PresenceTracker` uses via `get_redis()` per call.
        self._redis = redis

    def _client(self) -> Redis[str]:
        return self._redis if self._redis is not None else get_redis()

    async def put(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        surface: str,
        key: str | None,
        content: str,
    ) -> bool:
        """Store or refresh one draft. Returns whether anything was written.

        Refuses an unknown surface and an activity surface with no key: both are
        malformed frames, and storing them would create an entry nothing can clear.

        **Empty content clears rather than stores.** A participant who selects all and
        deletes has retracted the draft, and an empty string left in Redis would be
        returned to an agent as "they are composing" for the next fifteen minutes.
        """
        if surface not in SURFACES:
            return False
        if surface == ACTIVITY and not key:
            return False
        if key is not None and normalise_key(key) != key:
            return False
        if not content.strip():
            await self.clear(room_id=room_id, user_id=user_id, surface=surface, key=key)
            return False

        stored, truncated = clip(content)
        entry_key = _entry_key(room_id, user_id, surface, key)
        payload = json.dumps(
            {"content": stored, "updated_at": now().isoformat(), "truncated": truncated},
            ensure_ascii=False,
        )
        try:
            r = self._client()
            if await self._over_user_budget(r, room_id, user_id, entry_key, len(stored)):
                # The per-user ceiling is enforced by refusing the *new* value, not by
                # evicting an older surface: evicting would let a participant's chat
                # draft silently delete their worksheet draft, which is a data loss the
                # participant cannot see and did not cause.
                # Room only, never the user. [R32.06] puts participant identifiers
                # off the log trail as well as off the audit trail, and a user id
                # here would say "this person is typing a lot in this room" to
                # anyone with log access — which is most of what the feature is
                # supposed to require a grant for.
                logger.info(
                    "a draft update in room %s exceeded the per-user budget; not stored",
                    room_id,
                )
                return False
            pipe = r.pipeline(transaction=False)
            pipe.set(entry_key, payload, ex=DRAFT_TTL_SECONDS)
            pipe.sadd(_index_key(room_id), entry_key)
            pipe.expire(_index_key(room_id), _INDEX_TTL_SECONDS)
            await pipe.execute()
            return True
        except Exception:
            logger.warning("draft store write failed for room %s", room_id, exc_info=True)
            return False

    async def _over_user_budget(
        self, r: Redis[str], room_id: uuid.UUID, user_id: uuid.UUID, entry_key: str, incoming: int
    ) -> bool:
        """Would this write push one participant past their budget in this room?

        Two ceilings, and both are needed: ``MAX_USER_CHARS`` bounds how much they
        can store, ``MAX_USER_ENTRIES`` bounds how many keys they can create. The
        second is not implied by the first — a thousand one-character drafts under a
        thousand distinct activity keys sit well inside the byte budget while making
        the read path's MGET a thousand keys wide.

        Measured against the participant's *other* surfaces, so replacing a draft with
        a longer one of the same size class never trips it — otherwise a student whose
        worksheet already fills the budget could never edit it again.

        Fails **open** (returns False) on a read error: the per-surface cap already
        bounds any single value, so a Redis hiccup costs the aggregate ceiling for one
        write rather than the participant's draft.
        """
        try:
            members = await r.smembers(_index_key(room_id))
        except Exception:
            return False
        prefix = f"ws:draft:{room_id}:{user_id}:"
        others = [m for m in members if m.startswith(prefix) and m != entry_key]
        if not others:
            return incoming > MAX_USER_CHARS
        # Counted before the values are fetched: the count cap is what stops a
        # client minting entries, and reading a thousand of them to decide to
        # refuse the thousand-and-first would be doing the work the cap exists to
        # prevent. `others` excludes `entry_key`, so replacing an existing draft is
        # never refused by this -- only a genuinely new key is.
        if len(others) >= MAX_USER_ENTRIES:
            return True
        try:
            values = await r.mget(others)
        except Exception:
            return False
        used = 0
        for raw in values:
            entry = _decode(raw)
            if entry is not None:
                used += len(str(entry.get("content") or ""))
        return used + incoming > MAX_USER_CHARS

    async def clear(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        surface: str,
        key: str | None,
    ) -> None:
        """Drop one draft. Called on send, on submit, and on an explicit clear.

        **Not called on disconnect**, deliberately: a tab reload would otherwise
        destroy a real draft the participant still has on screen. The TTL is what
        bounds a vanished participant, and every returned entry carries its age so a
        reader can tell the two apart.
        """
        if surface not in SURFACES:
            return
        entry_key = _entry_key(room_id, user_id, surface, key)
        try:
            r = self._client()
            pipe = r.pipeline(transaction=False)
            pipe.delete(entry_key)
            pipe.srem(_index_key(room_id), entry_key)
            await pipe.execute()
        except Exception:
            logger.warning("draft clear failed for room %s", room_id, exc_info=True)

    async def clear_room(self, room_id: uuid.UUID) -> None:
        """Drop every draft in one room. The feature's whole data footprint.

        Exists so "deleting the room's Redis keys deletes everything this feature ever
        stored" is an operation rather than a claim (§8).
        """
        try:
            r = self._client()
            members = await r.smembers(_index_key(room_id))
            pipe = r.pipeline(transaction=False)
            for member in members:
                pipe.delete(member)
            pipe.delete(_index_key(room_id))
            await pipe.execute()
        except Exception:
            logger.warning("draft room clear failed for room %s", room_id, exc_info=True)

    async def list_for_room(self, room_id: uuid.UUID) -> list[DraftEntry]:
        """Every live draft in one room, newest first. Empty on any failure.

        One `SMEMBERS` plus one `MGET`, never a `SCAN`: a scan over a shared keyspace
        costs the whole database per call and this runs on an agent's turn.

        **The index is reconciled against the entries**, mirroring
        `presence._reconcile_roster`: an entry may expire or be evicted (`allkeys-lru`)
        while its index member remains, and a member with no value is dropped from the
        set here rather than accumulating until the index's own TTL. An evicted draft
        therefore reads as absent, never as stale — which is the property that makes
        eviction a bounded loss rather than wrong data.
        """
        try:
            r = self._client()
            members = sorted(await r.smembers(_index_key(room_id)))
            if not members:
                return []
            values = await r.mget(members)
        except Exception:
            logger.warning("draft room read failed for room %s", room_id, exc_info=True)
            return []

        current = now()
        entries: list[DraftEntry] = []
        stale: list[str] = []
        for member, raw in zip(members, values, strict=True):
            entry = _decode(raw)
            parsed = _parse_key(member, room_id)
            if entry is None or parsed is None:
                stale.append(member)
                continue
            user_id, surface, key = parsed
            updated = _parse_ts(entry.get("updated_at"))
            age = 0 if updated is None else max(int((current - updated).total_seconds()), 0)
            entries.append(
                DraftEntry(
                    user_id=user_id,
                    surface=surface,
                    key=key,
                    content=str(entry.get("content") or ""),
                    age_seconds=age,
                    truncated=bool(entry.get("truncated")),
                )
            )
        if stale:
            try:
                evict = self._client().pipeline(transaction=False)
                for member in stale:
                    evict.srem(_index_key(room_id), member)
                await evict.execute()
            except Exception:
                logger.warning("draft index reconcile failed for room %s", room_id, exc_info=True)
        entries.sort(key=lambda e: e.age_seconds)
        return entries


def _decode(raw: Any) -> dict[str, Any] | None:
    """A stored payload, or ``None`` for anything unusable.

    Total: the value is JSON this module wrote, but a partial write, a manual edit or a
    format change from a future version must degrade to "no draft" rather than to an
    exception on an agent's turn.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_key(member: str, room_id: uuid.UUID) -> tuple[uuid.UUID, str, str | None] | None:
    """``(user_id, surface, key)`` from an index member, or ``None``.

    The member is a key this module composed, so this is a decode rather than a parse
    of foreign input — but it is validated anyway, because the index is the one place
    a key from an older format or another room could survive a deploy, and a
    mis-attributed draft is worse than a dropped one.
    """
    prefix = f"ws:draft:{room_id}:"
    if not member.startswith(prefix):
        return None
    rest = member[len(prefix) :].split(":")
    if len(rest) not in (2, 3):
        return None
    try:
        user_id = uuid.UUID(rest[0])
    except (AttributeError, TypeError, ValueError):
        return None
    surface = rest[1]
    if surface not in SURFACES:
        return None
    key = rest[2] if len(rest) == 3 else None
    if surface == ACTIVITY and not key:
        return None
    return user_id, surface, key


def _parse_ts(raw: Any) -> datetime | None:
    """A stored `updated_at`, read as UTC-aware.

    `as_utc` is not decoration: `now()` is aware, and subtracting a naive value from
    an aware one raises. The stored string is always aware because `now()` wrote it,
    but a value that survived a clock-source change must degrade to "age 0" rather
    than raise on an agent's turn.
    """
    if not isinstance(raw, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


__all__ = [
    "ACTIVITY",
    "COMPOSER",
    "DRAFT_TTL_SECONDS",
    "MAX_CONTENT_CHARS",
    "MAX_USER_CHARS",
    "MAX_USER_ENTRIES",
    "SURFACES",
    "TRUNCATION_MARKER",
    "DraftEntry",
    "DraftStore",
    "clip",
    "normalise_key",
]
