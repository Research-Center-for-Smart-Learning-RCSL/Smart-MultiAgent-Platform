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


async def _run_remote_validator(
    db: Any, activity_type: ActivityType, submission: ActivitySubmission
) -> ValidationResult:
    """Dispatch an mcp/webhook validator via AgentsFacade and map its output to a
    :class:`ValidationResult`. Raises :class:`ValidatorUnavailable` when the
    validator could not produce a verdict (→ ``error``)."""
    from contexts.agents.interfaces.facade import AgentsFacade

    agents = AgentsFacade(db)
    cfg = activity_type.validator_config
    envelope = {"payload": submission.payload, "activity_type_key": activity_type.key}

    if activity_type.validator_kind is ValidatorKind.MCP:
        res = await agents.invoke_mcp_tool(
            project_id=activity_type.project_id,
            agent_id=uuid.UUID(str(cfg["agent_id"])),
            binding_id=uuid.UUID(str(cfg["binding_id"])),
            tool_name=str(cfg["tool_name"]),
            arguments=envelope,
        )
        if not res.ok:
            raise ValidatorUnavailable(res.stderr or "mcp validator failed")
        return result_from_json(res.stdout)

    if activity_type.validator_kind is ValidatorKind.WEBHOOK:
        resp = await agents.egress_request(
            project_id=activity_type.project_id,
            method="POST",
            url=str(cfg["url"]),
            body=envelope,
        )
        if resp.blocked is not None or resp.status is None or not (200 <= resp.status < 400):
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


async def validate_activity_submission(ctx: dict[str, Any], submission_id: str) -> str:
    """Run the async (mcp/webhook) validator for one submission and write back."""
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    sid = uuid.UUID(str(submission_id))
    result_status = "skipped"
    chatroom_id: uuid.UUID | None = None

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

    if chatroom_id is not None and result_status in ("validated", "error"):
        await _emit_validated(chatroom_id, sid, result_status)
    return result_status


async def activities_watchdog(ctx: dict[str, Any]) -> str:
    """Sweep ``pending`` submissions older than the TTL to ``error`` (R30.06)."""
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel import audit
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        swept = await ActivitiesFacade(db).sweep_stalled(ttl_seconds=_PENDING_TTL_SECONDS)
        if swept:
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="activity.watchdog_swept",
                    metadata={"rows_affected": swept, "ttl_seconds": _PENDING_TTL_SECONDS},
                ),
            )
        await db.commit()
        await flush_tail_events(db)

    logger.bind(event="activities_watchdog_done", swept=swept).info(
        f"activities watchdog: swept {swept} stalled submissions"
    )
    return f"swept={swept}"


__all__ = ["activities_watchdog", "validate_activity_submission"]
