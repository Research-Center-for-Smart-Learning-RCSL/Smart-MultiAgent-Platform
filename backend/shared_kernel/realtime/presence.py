"""Backward-compat re-exports -- moved to contexts.conversation.infrastructure.presence.

DEPRECATED: import from contexts.conversation.infrastructure.presence instead.
"""

from contexts.conversation.infrastructure.presence import (
    PresenceTracker,
    scrub_stale_presence,
)

__all__ = ["PresenceTracker", "scrub_stale_presence"]
