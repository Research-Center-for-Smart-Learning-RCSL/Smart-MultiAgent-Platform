"""Backward-compat re-exports — moved to contexts.agents.infrastructure.turn_lock.

The generic distributed lock lives in shared_kernel.realtime.distributed_lock.
The agent-specific key builder and context manager live in
contexts.agents.infrastructure.turn_lock.

DEPRECATED: import from contexts.agents.infrastructure.turn_lock instead.
"""

from contexts.agents.infrastructure.turn_lock import (
    DEFAULT_TURN_TTL_S,
    turn_lock,
    turn_lock_key,
)

__all__ = [
    "DEFAULT_TURN_TTL_S",
    "turn_lock",
    "turn_lock_key",
]
