"""Instruct chain context (G.7 / R15.16) — cross-turn chain propagation.

An ``instruct`` runs the target agent's turn inline in the A2A consumer loop. If
that turn issues a *further* instruct, it must continue the SAME chain (chain_id
+ path) rather than mint a fresh one, or the loop/depth/budget guards (R15.16)
reset on every hop and never fire.

The chain is threaded through a ``contextvars`` variable bound by the inbox
handler (:func:`a2a_handler._handle_instruct`) for the turn's duration — the same
pattern as :mod:`a2a_call_chain` for synchronous CALLs. :meth:`InstructService.
issue` reads it as the fallback chain when the caller supplies none, so an
in-turn instruct inherits the delivered envelope's chain automatically.

Contextvars propagate across ``await`` and into child tasks / ``to_thread``
workers, so any in-task path the turn takes inherits the chain.
"""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from collections.abc import Iterator

# (chain_id, parent_path): chain_id is the current instruct chain (None at root,
# i.e. no enclosing instruct); parent_path is the ordered tuple of issuer agent
# ids traversed so far — the delivered envelope's ``path``.
_chain: contextvars.ContextVar[tuple[uuid.UUID | None, tuple[uuid.UUID, ...]]] = contextvars.ContextVar(
    "instruct_chain",
    default=(None, ()),
)


def current() -> tuple[uuid.UUID | None, tuple[uuid.UUID, ...]]:
    """The (chain_id, parent_path) of the instruct in scope, or (None, ()) at root."""
    return _chain.get()


@contextlib.contextmanager
def enter(chain_id: uuid.UUID | None, parent_path: tuple[uuid.UUID, ...]) -> Iterator[None]:
    """Bind the instruct chain for the duration of a delivered instruct's turn."""
    token = _chain.set((chain_id, tuple(parent_path)))
    try:
        yield
    finally:
        _chain.reset(token)


__all__ = ["current", "enter"]
