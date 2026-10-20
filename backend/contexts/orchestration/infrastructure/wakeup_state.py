"""Redis-backed wake-up state tracking (G.3).

Maintains per-binding counters and timers that the wake-up evaluator reads to
decide whether to fire a trigger. Every cadence key carries the binding id
([R15.09]): an unbind and rebind of the same agent is a new incarnation, and its
counters, clock and deferred firing start fresh instead of reviving the old ones.
The old incarnation's keys are simply never read again and expire.

Keys (`{b}` = `{agent_id}:{room_id}:{binding_id}`):
  wakeup:msg_count:{b}  — message counter (for every_n_messages)
  wakeup:silence_ts:{b} — last-activity timestamp (for silence_minutes)
  wakeup:autostop:{b}   — consecutive agent-only rounds
  wakeup:msg_due:{b}    — every_n firing deferred past the agent's own message
  wakeup:gated_notice:{agent_id}:{room_id} — owner-notice debounce (not cadence)

SoC: pure Redis state; no domain logic, no DB access.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from shared_kernel.auth.clients import get_redis

_KEY_TTL: Final = 86400 * 7  # 7 days — stale keys auto-expire


def _msg_count_key(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> str:
    return f"wakeup:msg_count:{agent_id}:{room_id}:{binding_id}"


def _silence_ts_key(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> str:
    return f"wakeup:silence_ts:{agent_id}:{room_id}:{binding_id}"


def _autostop_key(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> str:
    return f"wakeup:autostop:{agent_id}:{room_id}:{binding_id}"


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
    binding_id: uuid.UUID,
) -> int:
    """Increment and return the message counter for this agent+room."""
    r = get_redis()
    key = _msg_count_key(agent_id, room_id, binding_id)
    pipe = r.pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, _KEY_TTL)
    results = await pipe.execute()
    return int(results[0])


async def get_message_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> int:
    val = await get_redis().get(_msg_count_key(agent_id, room_id, binding_id))
    return int(val) if val else 0


async def restart_message_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> None:
    """Start the every_n cycle again: the counter and any deferred firing belong
    to the cadence that just changed."""
    await get_redis().delete(
        _msg_count_key(agent_id, room_id, binding_id), _fire_due_key(agent_id, room_id, binding_id)
    )


def _fire_due_key(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> str:
    return f"wakeup:msg_due:{agent_id}:{room_id}:{binding_id}"


async def set_fire_due(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> None:
    """The N-th message was the agent's own: fire on the next one ([R15.01a])."""
    await get_redis().set(_fire_due_key(agent_id, room_id, binding_id), "1", ex=_KEY_TTL)


async def pop_fire_due(agent_id: uuid.UUID, room_id: uuid.UUID, binding_id: uuid.UUID) -> bool:
    """Consume a deferred firing; True when one was pending."""
    return bool(await get_redis().getdel(_fire_due_key(agent_id, room_id, binding_id)))


# ---------------------------------------------------------------------------
# silence_minutes timer
# ---------------------------------------------------------------------------


async def touch_silence_timestamp(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> None:
    """Record 'now' as the last activity time for silence detection."""
    r = get_redis()
    key = _silence_ts_key(agent_id, room_id, binding_id)
    await r.set(key, datetime.now(UTC).isoformat(), ex=_KEY_TTL)


async def get_silence_timestamp(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> datetime | None:
    raw = await get_redis().get(_silence_ts_key(agent_id, room_id, binding_id))
    if not raw:
        return None
    return datetime.fromisoformat(str(raw))


# ---------------------------------------------------------------------------
# autostop_rounds counter
# ---------------------------------------------------------------------------


async def increment_autostop(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> int:
    """Increment consecutive agent-only rounds. Returns new count."""
    r = get_redis()
    key = _autostop_key(agent_id, room_id, binding_id)
    pipe = r.pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, _KEY_TTL)
    results = await pipe.execute()
    return int(results[0])


async def reset_autostop(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> None:
    """User sent a message — reset autostop counter."""
    await get_redis().delete(_autostop_key(agent_id, room_id, binding_id))


async def get_autostop_count(
    agent_id: uuid.UUID,
    room_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> int:
    val = await get_redis().get(_autostop_key(agent_id, room_id, binding_id))
    return int(val) if val else 0


__all__ = [
    "get_autostop_count",
    "get_message_count",
    "get_silence_timestamp",
    "increment_autostop",
    "increment_message_count",
    "pop_fire_due",
    "reset_autostop",
    "restart_message_count",
    "set_fire_due",
    "touch_silence_timestamp",
]
