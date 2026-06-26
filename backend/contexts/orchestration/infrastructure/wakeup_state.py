"""Redis-backed wake-up state tracking (G.3).

Maintains per-agent, per-room counters and timers that the wake-up
evaluator reads to decide whether to fire a trigger.

Keys:
  wakeup:msg_count:{agent_id}:{room_id}    — message counter (for every_n_messages)
  wakeup:silence_timer:{agent_id}:{room_id} — last-activity timestamp (for silence_minutes)
  wakeup:autostop:{agent_id}:{room_id}     — consecutive agent-only rounds
  wakeup:silence_active:{agent_id}:{room_id} — "1" if silence timer is running

SoC: pure Redis state; no domain logic, no DB access.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from shared_kernel.auth.clients import get_redis

_KEY_TTL: Final = 86400 * 7  # 7 days — stale keys auto-expire


def _msg_count_key(agent_id: uuid.UUID, room_id: uuid.UUID) -> str:
    return f"wakeup:msg_count:{agent_id}:{room_id}"


def _silence_ts_key(agent_id: uuid.UUID, room_id: uuid.UUID) -> str:
    return f"wakeup:silence_ts:{agent_id}:{room_id}"


def _autostop_key(agent_id: uuid.UUID, room_id: uuid.UUID) -> str:
    return f"wakeup:autostop:{agent_id}:{room_id}"


def _silence_active_key(agent_id: uuid.UUID, room_id: uuid.UUID) -> str:
    return f"wakeup:silence_active:{agent_id}:{room_id}"


def _gated_notice_key(agent_id: uuid.UUID, room_id: uuid.UUID) -> str:
    return f"wakeup:gated_notice:{agent_id}:{room_id}"


# ---------------------------------------------------------------------------
# presence-gated wake-up notice debounce
# ---------------------------------------------------------------------------


async def claim_gated_notice(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    cooldown_s: int = 3600,
) -> bool:
    """Atomically claim the right to emit one "wake-up was presence-gated"
    notice for this agent+room. Returns True at most once per ``cooldown_s``
    window (Redis ``SET NX EX``) so a busy room cannot spam owners on every
    Nth message. Returns False while the cooldown is still in effect.
    """
    r = get_redis()
    key = _gated_notice_key(agent_id, room_id)
    return bool(await r.set(key, "1", nx=True, ex=cooldown_s))


async def release_gated_notice(agent_id: uuid.UUID, room_id: uuid.UUID) -> None:
    """Drop the debounce token so the next presence-gated wake-up can re-notify.

    Used when a claimed notice failed to deliver — without this a transient
    failure would silence the heads-up for the full cooldown window.
    """
    r = get_redis()
    await r.delete(_gated_notice_key(agent_id, room_id))


# ---------------------------------------------------------------------------
# every_n_messages counter
# ---------------------------------------------------------------------------


async def increment_message_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> int:
    """Increment and return the message counter for this agent+room."""
    r = get_redis()
    key = _msg_count_key(agent_id, room_id)
    count = await r.incr(key)
    await r.expire(key, _KEY_TTL)
    return int(count)


async def reset_message_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> None:
    await get_redis().delete(_msg_count_key(agent_id, room_id))


async def get_message_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> int:
    val = await get_redis().get(_msg_count_key(agent_id, room_id))
    return int(val) if val else 0


# ---------------------------------------------------------------------------
# silence_minutes timer
# ---------------------------------------------------------------------------


async def touch_silence_timestamp(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> None:
    """Record 'now' as the last activity time for silence detection."""
    r = get_redis()
    key = _silence_ts_key(agent_id, room_id)
    await r.set(key, datetime.now(UTC).isoformat(), ex=_KEY_TTL)


async def get_silence_timestamp(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> datetime | None:
    raw = await get_redis().get(_silence_ts_key(agent_id, room_id))
    if not raw:
        return None
    return datetime.fromisoformat(str(raw))


async def set_silence_active(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    active: bool,
) -> None:
    """Start or pause the silence timer based on presence (R15.05b)."""
    r = get_redis()
    key = _silence_active_key(agent_id, room_id)
    if active:
        await r.set(key, "1", ex=_KEY_TTL)
    else:
        await r.delete(key)


async def is_silence_active(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> bool:
    val = await get_redis().get(_silence_active_key(agent_id, room_id))
    return val == "1"


# ---------------------------------------------------------------------------
# autostop_rounds counter
# ---------------------------------------------------------------------------


async def increment_autostop(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> int:
    """Increment consecutive agent-only rounds. Returns new count."""
    r = get_redis()
    key = _autostop_key(agent_id, room_id)
    count = await r.incr(key)
    await r.expire(key, _KEY_TTL)
    return int(count)


async def reset_autostop(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> None:
    """User sent a message — reset autostop counter."""
    await get_redis().delete(_autostop_key(agent_id, room_id))


async def get_autostop_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
) -> int:
    val = await get_redis().get(_autostop_key(agent_id, room_id))
    return int(val) if val else 0


__all__ = [
    "get_autostop_count",
    "get_message_count",
    "get_silence_timestamp",
    "increment_autostop",
    "increment_message_count",
    "is_silence_active",
    "reset_autostop",
    "reset_message_count",
    "set_silence_active",
    "touch_silence_timestamp",
]
