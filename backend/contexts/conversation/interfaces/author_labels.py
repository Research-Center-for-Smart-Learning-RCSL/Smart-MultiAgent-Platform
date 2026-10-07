"""Chat-author label precedence, re-exported for callers outside the context.

The rule lives in ``contexts.conversation.domain.author_labels``.
"""

from __future__ import annotations

from contexts.conversation.domain.author_labels import prefer_guest_label

__all__ = ["prefer_guest_label"]
