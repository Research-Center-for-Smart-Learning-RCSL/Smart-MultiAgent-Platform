"""Synchronous A2A call-chain tracking (R9.15 — depth + cycle guard).

A CALL runs the callee's turn *inline* in its consumer loop (one loop per
agent), so a mutual call A->B->A stalls both loops to their rendezvous timeout
and a deep A->B->C... chain pins one worker task per level. Unlike instruct,
the CALL envelope historically carried no depth/path.

We thread the chain through a ``contextvars`` variable so any A2A call issued
*while a CALL-triggered turn is running* inherits the parent depth/path —
regardless of whether the turn issues it via a tool or any other in-task path
(contextvars propagate across ``await`` and are copied into child tasks /
``to_thread`` workers). The transport stamps the computed depth/path onto the
outgoing envelope (:class:`A2AEnvelope`), and the inbox handler re-enters the
chain from the envelope before running the callee's turn.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

from contexts.orchestration.domain.errors import A2ACallDepthExceeded, A2ACallLoop
from contexts.orchestration.domain.models import A2A_CALL_MAX_DEPTH

# (depth, path): depth is the 1-based nesting level of the current synchronous
# CALL; path is the ordered tuple of callee agent-id strings on the call stack.
_chain: contextvars.ContextVar[tuple[int, tuple[str, ...]]] = contextvars.ContextVar(
    "a2a_call_chain",
    default=(0, ()),
)


def current() -> tuple[int, tuple[str, ...]]:
    """The (depth, path) of the synchronous CALL in scope, or (0, ()) at root."""
    return _chain.get()


def next_hop(callee: str) -> tuple[int, tuple[str, ...]]:
    """Compute (depth, path) for a CALL to ``callee`` from the current chain.

    Raises :class:`A2ACallLoop` if ``callee`` is already on the call stack, or
    :class:`A2ACallDepthExceeded` if the hop would exceed the hard depth cap —
    so the recursive/over-deep call is rejected before it can stall a loop.
    """
    depth, path = _chain.get()
    if callee in path:
        raise A2ACallLoop(f"a2a call cycle: {callee} already on the call stack {list(path)}")
    new_depth = depth + 1
    if new_depth > A2A_CALL_MAX_DEPTH:
        raise A2ACallDepthExceeded(
            f"a2a call depth {new_depth} exceeds max {A2A_CALL_MAX_DEPTH} (path={list(path)})"
        )
    return new_depth, (*path, callee)


@contextlib.contextmanager
def enter(depth: int, path: tuple[str, ...]) -> Iterator[None]:
    """Bind the chain for the duration of a CALL-triggered turn (set + reset)."""
    token = _chain.set((depth, path))
    try:
        yield
    finally:
        _chain.reset(token)


__all__ = ["current", "enter", "next_hop"]
