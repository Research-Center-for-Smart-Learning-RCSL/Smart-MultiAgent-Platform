"""Context-management strategies — R9.09–R9.11.

Two strategies, **no others**:

- **general** — full history goes to the provider; if it exceeds the
  provider's context limit, the provider's own error is surfaced to the UI.
- **compact** — when the next request's projected token count would exceed
  ``context_token_cap`` (default = 75% of the provider's limit), run
  `/compact`: call the agent's own Key Group with a summarisation prompt,
  replace the oldest un-compacted range with a single system-role message
  tagged ``{"type": "compact_summary"}``. User-facing transcript is never
  altered — only the model-facing history changes.

SoC:

- This module is **pure orchestration logic**. It does not open sessions
  to the provider, does not read messages, does not write messages. It
  exposes a small Protocol for a "summariser" and a Protocol for a
  "transcript store"; both are implemented elsewhere (conversation /
  provider router contexts). That keeps the circular import risk at zero.
- Audit writes are handled by the caller (conversation context), which
  has the `AsyncSession` already open. `/compact` failure merely raises
  `CompactFailed`; the caller logs `agent.compact_failed` (R9.11) and
  drops back to the un-compacted history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

__all__ = [
    "CompactFailed",
    "CompactPlan",
    "MessageLike",
    "Summariser",
    "TranscriptStore",
    "choose_range_to_compact",
    "default_cap_from_limit",
    "should_compact",
]


# ---------------------------------------------------------------------------
# Protocols (no concrete imports — lets us type without pulling conversation)
# ---------------------------------------------------------------------------


class MessageLike(Protocol):
    """Minimum fields the compactor needs off a chat message."""

    id: Any
    role: str  # "user" | "agent" | "system" | "tool"
    content: str
    metadata: dict[str, Any]
    token_count: int


class Summariser(Protocol):
    async def summarise(self, messages: list[MessageLike], *, max_tokens: int = 2000) -> str:
        """Return a summary preserving decisions, file paths, open questions."""


class TranscriptStore(Protocol):
    """Replace a contiguous range of messages with a compact-summary row."""

    async def replace_range_with_summary(
        self,
        *,
        message_ids: list[Any],
        summary_text: str,
    ) -> Any:
        """Atomic: delete the range from the *model-facing* view, insert a
        ``system`` role message with ``metadata={"type":"compact_summary"}``
        carrying ``summary_text``, and return the new message id.

        Implementations must NOT touch user-visible copies (R9.10) —
        user-facing rendering pulls from a separate projection.
        """


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------


def default_cap_from_limit(provider_context_limit: int) -> int:
    """R9.10 — default `context_token_cap` is 75% of the provider limit."""
    if provider_context_limit <= 0:
        raise ValueError("provider_context_limit must be positive")
    return (provider_context_limit * 3) // 4


def should_compact(
    *,
    mode: Literal["general", "compact"],
    projected_tokens: int,
    context_token_cap: int | None,
    provider_context_limit: int,
) -> bool:
    """True iff the next request would cross the cap under ``compact``."""
    if mode != "compact":
        return False
    cap = (
        context_token_cap if context_token_cap is not None else default_cap_from_limit(provider_context_limit)
    )
    return projected_tokens > cap


@dataclass(frozen=True, slots=True)
class CompactPlan:
    """Describes the range to be summarised in one compaction run."""

    message_ids: list[Any]
    total_tokens: int


def choose_range_to_compact(
    messages: list[MessageLike],
    *,
    target_tokens_to_shed: int,
) -> CompactPlan | None:
    """Pick the **oldest un-compacted contiguous range** whose token sum is
    at least ``target_tokens_to_shed``.

    Rules:
    - Never fold an existing `compact_summary` row into a new summary —
      that would chain compaction and blur provenance. Skip past them.
    - Always start at the first un-compacted message; extend forward until
      the running token total meets the target.
    - If the un-compacted tail has fewer tokens than the target, still
      pick that range (caller decides whether to proceed).
    - If no un-compacted messages exist, return None (nothing to do).
    """
    if target_tokens_to_shed <= 0:
        return None

    picked: list[Any] = []
    running = 0
    started = False

    for msg in messages:
        is_compact = isinstance(msg.metadata, dict) and msg.metadata.get("type") == "compact_summary"
        if is_compact and not started:
            # Skip over earlier summaries until we find fresh material.
            continue
        if is_compact and started:
            # We hit another summary after starting — stop; a fresh range
            # must not jump over a prior summary.
            break
        started = True
        picked.append(msg.id)
        running += int(msg.token_count or 0)
        if running >= target_tokens_to_shed:
            break

    if not picked:
        return None
    return CompactPlan(message_ids=picked, total_tokens=running)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CompactFailed(RuntimeError):
    """Raised by the compact pipeline when summarisation fails.

    Caller (conversation context) catches and:
      1. leaves the message history untouched (R9.11);
      2. emits ``audit.emit(agent.compact_failed, …)``.
    """


# ---------------------------------------------------------------------------
# High-level orchestration helper
# ---------------------------------------------------------------------------


async def run_compact(
    *,
    messages: list[MessageLike],
    projected_tokens: int,
    context_token_cap: int | None,
    provider_context_limit: int,
    summariser: Summariser,
    store: TranscriptStore,
    max_summary_tokens: int = 2000,
) -> bool:
    """Execute one `/compact` round.

    Returns True on success (a summary replaced a range); False if no
    compaction was needed. Raises :class:`CompactFailed` on summariser
    error so the caller can run the audit path.
    """
    if not should_compact(
        mode="compact",
        projected_tokens=projected_tokens,
        context_token_cap=context_token_cap,
        provider_context_limit=provider_context_limit,
    ):
        return False

    cap = (
        context_token_cap if context_token_cap is not None else default_cap_from_limit(provider_context_limit)
    )
    target = max(projected_tokens - cap, 1)
    plan = choose_range_to_compact(messages, target_tokens_to_shed=target)
    if plan is None:
        return False

    range_messages = [m for m in messages if m.id in set(plan.message_ids)]
    try:
        summary = await summariser.summarise(range_messages, max_tokens=max_summary_tokens)
    except Exception as exc:  # — summariser surface is open-ended
        raise CompactFailed(str(exc)) from exc

    await store.replace_range_with_summary(
        message_ids=plan.message_ids,
        summary_text=summary,
    )
    return True
