"""A2A scope checker — R9.17.

Decides whether agent ``caller`` is allowed to invoke agent ``callee``.
The logic is intentionally a pure function over already-fetched data:

1. Same project. Cross-project calls are always denied.
2. Both agents have ``a2a_enabled = true``.
3. Target project is not soft-deleted.
4. At least ONE of:
   a. Caller's invocation context (chatroom or workflow_run) is a context
      the callee is also attached to.
   b. Callee has ``wakeup_config.triggers.call_only.enabled = true``.

SoC:

- This module does NOT hit the DB. The caller (F / G / H contexts) loads
  the two `Agent` rows, the callee's *attached context ids*, and the
  invoker's *current context id* via existing facades, and passes them
  into :func:`evaluate`.
- Audit writes (`a2a.forbidden`) are written by the caller from its open
  session — same rule we use in the error-mapping layer.
- The result is either ``None`` (allowed) or an :class:`A2AForbidden`
  *ready to raise*; the caller raises or records as appropriate.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from contexts.agents.domain.errors import A2AForbidden
from contexts.agents.domain.models import Agent

__all__ = [
    "A2ACheckInput",
    "A2AVerdict",
    "evaluate",
    "is_call_only_enabled",
]


@dataclass(frozen=True, slots=True)
class A2ACheckInput:
    caller: Agent
    callee: Agent
    callee_attached_context_ids: frozenset[uuid.UUID]
    caller_invocation_context_id: uuid.UUID | None
    callee_project_deleted: bool


@dataclass(frozen=True, slots=True)
class A2AVerdict:
    allowed: bool
    reason: str


def is_call_only_enabled(agent: Agent) -> bool:
    """Parse ``wakeup_config.triggers.call_only.enabled`` safely.

    The JSONB column is free-form, so we defensively walk the path.
    """
    triggers = agent.wakeup_config.get("triggers") if agent.wakeup_config else None
    if not isinstance(triggers, dict):
        return False
    call_only = triggers.get("call_only")
    if not isinstance(call_only, dict):
        return False
    return bool(call_only.get("enabled", False))


def evaluate(ipt: A2ACheckInput) -> A2AVerdict:
    """Pure function. Returns a verdict; caller acts on ``allowed``.

    Implementation follows the R9.17 rule list *in order* so the rejection
    reason is the *first* rule that failed. This gives operators a clean
    debug trail in audit logs.
    """
    if ipt.caller.project_id != ipt.callee.project_id:
        return A2AVerdict(allowed=False, reason="cross-project")

    if ipt.callee_project_deleted:
        return A2AVerdict(allowed=False, reason="callee project soft-deleted")

    if not ipt.caller.a2a_enabled:
        return A2AVerdict(allowed=False, reason="caller a2a_enabled=false")
    if not ipt.callee.a2a_enabled:
        return A2AVerdict(allowed=False, reason="callee a2a_enabled=false")

    shared_context = (
        ipt.caller_invocation_context_id is not None
        and ipt.caller_invocation_context_id in ipt.callee_attached_context_ids
    )
    if shared_context:
        return A2AVerdict(allowed=True, reason="shared invocation context")

    if is_call_only_enabled(ipt.callee):
        return A2AVerdict(allowed=True, reason="callee call_only enabled")

    return A2AVerdict(
        allowed=False,
        reason="no shared context and callee call_only disabled",
    )


def enforce(ipt: A2ACheckInput) -> None:
    """Raise :class:`A2AForbidden` if the verdict denies the call.

    Thin sugar over :func:`evaluate` for callers that just want to let the
    error propagate to the RFC 7807 handler.
    """
    verdict = evaluate(ipt)
    if not verdict.allowed:
        raise A2AForbidden(
            f"a2a denied: {verdict.reason} " f"(caller={ipt.caller.id}, callee={ipt.callee.id})"
        )


def contexts_frozenset(ids: Iterable[uuid.UUID]) -> frozenset[uuid.UUID]:
    """Helper to avoid the caller remembering to wrap with ``frozenset``."""
    return frozenset(ids)
