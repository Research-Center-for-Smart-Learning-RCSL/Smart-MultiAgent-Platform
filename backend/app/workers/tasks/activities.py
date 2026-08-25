"""Async activity-validation worker + watchdog (Chapter §30, activities-platform-core §5.1).

A composition root: it may compose multiple context facades. It reads/writes the
activity submission through ``ActivitiesFacade`` and runs the slow mcp/webhook
validator **through** ``AgentsFacade`` (the SoC seam) — it never imports
``contexts/agents`` internals. The result is written back idempotently
(``record_validation`` transitions only from ``pending``), so an Arq at-least-once
redelivery or a race with the watchdog is a no-op.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from loguru import logger

from contexts.activities.application.validators.base import (
    ValidatorUnavailable,
    result_from_json,
)
from contexts.activities.domain.models import (
    ActivitySubmission,
    ActivityType,
    ValidationResult,
    ValidatorKind,
)

# TTL after which a still-``pending`` async validation is swept to ``error`` — the
# single safety net for a stalled worker OR a dropped post-commit enqueue.
_PENDING_TTL_SECONDS = 900

# Proposals expired per sweep tick. Bounded so one sweep cannot hold a lock over
# an unbounded set; the next tick takes the rest.
_EXPIRY_SWEEP_LIMIT = 200


async def _run_remote_validator(
    db: Any, activity_type: ActivityType, submission: ActivitySubmission
) -> ValidationResult:
    """Dispatch an mcp/webhook validator via AgentsFacade and map its output to a
    :class:`ValidationResult`. Raises :class:`ValidatorUnavailable` when the
    validator could not produce a verdict (→ ``error``)."""
    from contexts.agents.interfaces.facade import AgentsFacade

    # Both remote kinds are project-scoped by definition: an mcp validator's
    # agent/binding must live in one project ([R30.24]) and a webhook's egress
    # carries that project's allowlist and rate limit ([R30.07]). A platform-scoped
    # type has no project to supply, which is why `example_service` refuses to
    # install one with a remote validator. This is the second line of that defence,
    # reached only if a row got in some other way — an error verdict on one
    # submission, never a request dispatched against an arbitrary project.
    if activity_type.project_id is None:
        raise ValidatorUnavailable(
            f"platform-scoped activity type {activity_type.key!r} declares a "
            f"{activity_type.validator_kind.value} validator, which has no project to run in"
        )

    agents = AgentsFacade(db)
    cfg = activity_type.validator_config
    envelope = {"payload": submission.payload, "activity_type_key": activity_type.key}

    if activity_type.validator_kind is ValidatorKind.MCP:
        # A malformed config (validated at registration, but defend the dispatch
        # boundary too) maps to a clean error verdict instead of crashing the job.
        try:
            agent_id = uuid.UUID(str(cfg["agent_id"]))
            binding_id = uuid.UUID(str(cfg["binding_id"]))
            tool_name = str(cfg["tool_name"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidatorUnavailable(f"invalid mcp validator config: {exc}") from exc
        res = await agents.invoke_mcp_tool(
            project_id=activity_type.project_id,
            agent_id=agent_id,
            binding_id=binding_id,
            tool_name=tool_name,
            arguments=envelope,
        )
        if not res.ok:
            raise ValidatorUnavailable(res.stderr or "mcp validator failed")
        return result_from_json(res.stdout)

    if activity_type.validator_kind is ValidatorKind.WEBHOOK:
        url = cfg.get("url")
        if not url:
            raise ValidatorUnavailable("webhook validator config missing 'url'")
        resp = await agents.egress_request(
            project_id=activity_type.project_id,
            method="POST",
            url=str(url),
            body=envelope,
        )
        if resp.blocked is not None or resp.status is None or not (200 <= resp.status < 300):
            if resp.blocked is None and resp.status is not None and 300 <= resp.status < 400:
                raise ValidatorUnavailable(f"webhook status {resp.status} — {resp.redirect_detail}")
            raise ValidatorUnavailable(resp.blocked or f"webhook status {resp.status}")
        return result_from_json(resp.body)

    # in_process types are scored synchronously in SubmissionService; they never
    # enqueue this job.
    raise ValidatorUnavailable("in_process validator dispatched to the async worker")


async def _emit_validated(chatroom_id: uuid.UUID, submission_id: uuid.UUID, status: str) -> None:
    from contexts.conversation.interfaces import room_channel
    from shared_kernel.realtime.pubsub import Publisher

    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "activity.validated", {"submission_id": str(submission_id), "validation_status": status}
        )
    except Exception:  # best-effort — a dropped WS event never fails the job
        logger.bind(submission_id=str(submission_id)).warning("activity.validated emit failed", exc_info=True)


async def _emit_activity_signal(payload: dict[str, Any] | None) -> None:
    """Best-effort completion-emit of ``workflow_signal("activity", …)`` (R30.12) —
    the emit carrying the final ``error_class`` + rolling aggregate an impasse rule
    reacts to. A dropped signal never fails the validation job."""
    if payload is None:
        return
    from shared_kernel.queue import enqueue

    try:
        await enqueue("workflow_signal", "activity", payload)
    except Exception:
        logger.bind(submission_id=str(payload.get("submission_id"))).warning(
            "activity workflow-signal dispatch failed", exc_info=True
        )


async def validate_activity_submission(ctx: dict[str, Any], submission_id: str) -> str:
    """Run the async (mcp/webhook) validator for one submission and write back."""
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    sid = uuid.UUID(str(submission_id))
    result_status = "skipped"
    chatroom_id: uuid.UUID | None = None
    signal_payload: dict[str, Any] | None = None

    async with async_session() as db:
        facade = ActivitiesFacade(db)
        submission = await facade.get_submission(sid)
        if submission is None:
            return "missing"
        # Idempotency short-circuit: a redelivered job for an already-terminal row.
        if submission.validation_status.value != "pending":
            return "not-pending"
        chatroom_id = submission.chatroom_id

        activity_type = await facade.get_type(submission.activity_type_id)
        try:
            if activity_type is None:
                raise ValidatorUnavailable("activity type missing")
            start = time.monotonic()
            verdict = await _run_remote_validator(db, activity_type, submission)
            latency_ms = int((time.monotonic() - start) * 1000)
            changed = await facade.record_validation(submission_id=sid, result=verdict, latency_ms=latency_ms)
            result_status = "validated" if changed else "not-pending"
        except ValidatorUnavailable as exc:
            changed = await facade.record_validation_error(
                submission_id=sid, error_class="validator_unavailable"
            )
            result_status = "error" if changed else "not-pending"
            logger.bind(submission_id=str(sid)).info(f"activity validation unavailable: {exc}")
        await db.commit()
        await flush_tail_events(db)
        # Build the completion signal only when this delivery actually transitioned
        # the row (post-commit, so rolling counts the just-written verdict); a
        # redelivery that no-ops must not re-emit.
        if result_status in ("validated", "error"):
            signal_payload = await facade.build_activity_signal(submission_id=sid)

    if chatroom_id is not None and result_status in ("validated", "error"):
        await _emit_validated(chatroom_id, sid, result_status)
    await _emit_activity_signal(signal_payload)
    return result_status


async def activities_watchdog(ctx: dict[str, Any]) -> str:
    """Sweep ``pending`` submissions older than the TTL to ``error`` (R30.06), then
    notify each swept row exactly as the completion path does (F-20): one
    ``activity.validated`` room event and one ``activity`` workflow signal per row.
    The sweep is the safety net for a stalled worker OR a dropped enqueue, so it
    must emit the same frames that path would have."""
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel import audit
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    # (chatroom_id, submission_id, signal_payload) built post-commit, emitted
    # outside the session — same ordering as validate_activity_submission.
    to_emit: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any] | None]] = []
    async with async_session() as db:
        facade = ActivitiesFacade(db)
        swept = await facade.sweep_stalled(ttl_seconds=_PENDING_TTL_SECONDS)
        if swept:
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="activity.watchdog_swept",
                    metadata={"rows_affected": len(swept), "ttl_seconds": _PENDING_TTL_SECONDS},
                ),
            )
        await db.commit()
        await flush_tail_events(db)
        # Build each completion signal post-commit so the rolling aggregate counts
        # the just-written ``validation_timeout`` verdict (as the completion path
        # documents); a build failure for one row must not drop the others, so the
        # per-row build is guarded — the write-back is already committed.
        for submission_id, chatroom_id in swept:
            try:
                payload = await facade.build_activity_signal(submission_id=submission_id)
            except Exception:
                logger.bind(submission_id=str(submission_id)).warning(
                    "activity watchdog signal build failed", exc_info=True
                )
                continue
            to_emit.append((chatroom_id, submission_id, payload))

    # Both emits already swallow their own failures, so one bad row cannot fail the
    # sweep or block the rest.
    for chatroom_id, submission_id, payload in to_emit:
        await _emit_validated(chatroom_id, submission_id, "error")
        await _emit_activity_signal(payload)

    swept_count = len(swept)
    logger.bind(event="activities_watchdog_done", swept=swept_count).info(
        f"activities watchdog: swept {swept_count} stalled submissions"
    )
    return f"swept={swept_count}"


async def expire_group_proposals(ctx: dict[str, Any]) -> str:
    """Expire group proposals past their deadline ([R30.41]).

    THE BACKSTOP, NOT THE PRIMARY MECHANISM. Ending a round expires its
    proposals in the same transaction as the end (AC-9), which is what makes
    "a proposal can never produce a submission after its round finished" a
    property rather than a schedule. This catches the other case: a room whose
    facilitator never ended the round, where an open proposal would otherwise
    stay acceptable indefinitely.

    Bounded per tick, so one sweep cannot hold a lock over an unbounded set; the
    next tick takes the rest. Not audited per row -- nobody performed this act,
    and rows attributed to no actor are noise in an audit trail. The count is
    this log line.
    """
    from contexts.activities.interfaces.broadcast import dispatch_group_proposal_expired
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        expired = await ActivitiesFacade(db).expire_due_group_proposals(limit=_EXPIRY_SWEEP_LIMIT)
        await db.commit()

    # Post-commit, and each dispatch swallows its own failure, so one unreachable
    # room cannot cost the others their event.
    for proposal_id, chatroom_id, member_group_id in expired:
        await dispatch_group_proposal_expired(chatroom_id, proposal_id, member_group_id)

    count = len(expired)
    logger.bind(event="group_proposal_expiry_done", expired=count).info(
        f"group proposal sweep: expired {count} proposals"
    )
    return f"expired={count}"


__all__ = ["activities_watchdog", "expire_group_proposals", "validate_activity_submission"]
