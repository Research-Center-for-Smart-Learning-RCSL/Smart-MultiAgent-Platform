"""Workflow signal dispatch (K.4 — resume parked waits + start dormant triggers).

Two severed links the completeness audit found:

- ``wait_for_event`` nodes register ``wf:wait:by_event:{event_type}`` index sets
  and ``wf:wait:{run_id}:{node_id}`` claim keys, but *nothing* ever read them, so
  a parked branch could only ever resume via its timeout. This module is the
  missing consumer: given a real-world signal (a message landed, an A2A message
  arrived, a run variable changed) it finds every parked wait whose stored
  criteria match and enqueues a ``workflow_event_resume`` job (which claims the
  wait atomically via ``GETDEL`` — the same ASYNC-10 contract the timeout job
  uses, so an event and its timeout can never both resume one run).

- ``message_received`` / ``a2a_event`` / ``wakeup_signal`` trigger kinds were
  enum-only — declared in the schema, accepted by the linter, but no path ever
  *started* a run from them. ``find_triggered_workflows`` scans live workflows
  for a matching trigger node so the arq signal task can start one run each.

Matching is pure (``matches_*``) so it is unit-testable without Redis/DB; the
``find_*`` coroutines do the I/O. Nothing here commits — callers own that.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import re2
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pure matchers — config criteria vs an observed signal                        #
# --------------------------------------------------------------------------- #


def _regex_ok(pattern: str | None, content: str) -> bool:
    if not pattern:
        return True
    try:
        return re2.search(pattern, content) is not None
    except re2.error:
        pass
    try:
        return re.search(pattern, content, flags=re.DOTALL) is not None
    except (re.error, TimeoutError):
        logger.warning("workflow event dispatch: invalid content_regex %r", pattern)
        return False


def _sender_ok(sender_filter: str, sender_type: str) -> bool:
    return sender_filter in ("", "any") or sender_filter == sender_type


def matches_message(match: dict[str, Any], *, chatroom_id: str, sender_type: str, content: str) -> bool:
    """``message_in_room`` wait / ``message_received`` trigger criteria."""
    if str(match.get("chatroom_id", "")) != str(chatroom_id):
        return False
    if not _sender_ok(str(match.get("sender_filter", "any")), sender_type):
        return False
    return _regex_ok(match.get("content_regex"), content)


def matches_a2a(match: dict[str, Any], *, target_agent_id: str, msg_type: str) -> bool:
    """``a2a_message`` wait criteria (``target_agent_id`` + optional ``types``)."""
    if str(match.get("target_agent_id", "")) != str(target_agent_id):
        return False
    types = match.get("types")
    return not types or msg_type in types


def matches_a2a_trigger(match: dict[str, Any], *, agent_id: str, msg_type: str) -> bool:
    """``a2a_event`` trigger criteria (``agent_id`` + required ``event_types``)."""
    if str(match.get("agent_id", "")) != str(agent_id):
        return False
    event_types = match.get("event_types") or []
    return msg_type in event_types


def matches_variable(match: dict[str, Any], variables: dict[str, Any]) -> bool:
    """``variable_matches`` wait criteria — evaluate the stored SEL expression."""
    expression = match.get("expression", "")
    if not expression:
        return False
    from contexts.workflow.sel.evaluator import evaluate

    try:
        return bool(evaluate(expression, dict(variables)))
    except Exception:
        logger.warning("workflow variable_matches eval failed: %r", expression, exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Redis scan — parked waits matching a signal                                  #
# --------------------------------------------------------------------------- #


async def find_matching_waits(
    redis: Any,
    event_type: str,
    predicate: Any,
) -> list[tuple[str, str]]:
    """Return ``(run_id, node_id)`` for every parked ``event_type`` wait whose
    stored ``match`` criteria satisfy ``predicate(match)``.

    Read-only: the resume job does the atomic ``GETDEL`` claim, so a stale index
    member (its claim key already expired/consumed) is simply skipped here and
    pruned by the resume job's miss.
    """
    index_key = f"wf:wait:by_event:{event_type}"
    members = await redis.smembers(index_key)
    out: list[tuple[str, str]] = []
    for raw in members:
        member = raw.decode() if isinstance(raw, bytes) else str(raw)
        run_id, _, node_id = member.partition(":")
        if not node_id:
            continue
        payload = await redis.get(f"wf:wait:{run_id}:{node_id}")
        if payload is None:
            await redis.srem(index_key, member)  # prune a dangling index entry
            continue
        try:
            info = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if predicate(info.get("match", {})):
            out.append((run_id, node_id))
    return out


async def find_run_variable_waits(redis: Any, run_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(run_id, node_id, match)`` for ``variable_matches`` waits in one run."""
    index_key = "wf:wait:by_event:variable_matches"
    members = await redis.smembers(index_key)
    out: list[tuple[str, str, dict[str, Any]]] = []
    for raw in members:
        member = raw.decode() if isinstance(raw, bytes) else str(raw)
        rid, _, node_id = member.partition(":")
        if rid != str(run_id) or not node_id:
            continue
        payload = await redis.get(f"wf:wait:{rid}:{node_id}")
        if payload is None:
            continue
        try:
            info = json.loads(payload)
        except (ValueError, TypeError):
            continue
        out.append((rid, node_id, info.get("match", {})))
    return out


# --------------------------------------------------------------------------- #
# Workflow scan — dormant trigger nodes matching a signal                      #
# --------------------------------------------------------------------------- #


async def find_triggered_workflows(
    db: AsyncSession,
    trigger_type: str,
    predicate: Any,
) -> list[uuid.UUID]:
    """Return workflow ids whose (single) trigger node matches ``predicate``.

    Scans live workflows the way the cron scheduler does. O(workflows) per
    signal — acceptable for v1; a chatroom/agent→workflow Redis index is the
    documented optimisation when trigger volume warrants it.
    """
    from contexts.workflow.infrastructure.tables import workflows

    query = sa.select(workflows.c.id, workflows.c.definition).where(workflows.c.deleted_at.is_(None))
    rows = (await db.execute(query)).all()
    out: list[uuid.UUID] = []
    for row in rows:
        defn = row.definition or {}
        for node in defn.get("nodes", []):
            if node.get("type") != "trigger":
                continue
            config = node.get("config", {})
            if config.get("trigger_type") != trigger_type:
                continue
            if predicate(config):
                out.append(row.id)
            break  # rule 1: exactly one trigger node per workflow
    return out


__all__ = [
    "find_matching_waits",
    "find_run_variable_waits",
    "find_triggered_workflows",
    "matches_a2a",
    "matches_a2a_trigger",
    "matches_message",
    "matches_variable",
]
