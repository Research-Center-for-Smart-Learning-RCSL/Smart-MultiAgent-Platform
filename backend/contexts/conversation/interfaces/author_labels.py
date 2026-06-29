"""Chat-author label precedence — shared by the web member roster and the agent
turn engine so the rule lives in exactly one place.

The policy: a guest's per-room display name takes precedence over their account
label. The test is ``is not None`` (not truthiness) so an explicitly-set empty
per-room name is honoured rather than silently overridden by the account name.
"""

from __future__ import annotations


def prefer_guest_label(guest_label: str | None, account_label: str | None) -> str | None:
    """Return the room-guest label when set, else the account label."""
    return guest_label if guest_label is not None else account_label


__all__ = ["prefer_guest_label"]
