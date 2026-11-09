"""Async activity-validation worker + watchdog (Chapter §30, activities-platform-core §5.1).

A composition root: it may compose multiple context facades. It reads/writes the
activity submission through ``ActivitiesFacade`` and runs the slow mcp/webhook
validator **through** ``AgentsFacade`` (the SoC seam) — it never imports
``contexts/agents`` internals. The result is written back idempotently
(``record_validation`` transitions only from ``pending``), so an Arq at-least-once
redelivery or a race with the watchdog is a no-op.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from loguru import logger

from app.workers.tasks._agent_call import AgentCallFailed, AgentCallFailure, call_agent_once, resolve_agent
from contexts.activities.application.validators.base import (
    ValidatorUnavailable,
    result_from_json,
)
from contexts.activities.domain.creativity_judge import (
    JUDGE_BAD_OUTPUT,
    JUDGE_MISCONFIGURED,
    JUDGE_TIMEOUT,
    JUDGE_UNAVAILABLE,
    MAX_OUTPUT_TOKENS,
    SKIP_ANSWER_TOO_LARGE,
    SKIP_JUDGE_NOT_BOUND,
    JudgeAnswerTooLarge,
    JudgeOutputInvalid,
    JudgeStatus,
    build_judge_prompt,
    fluency,
    judge_agent_id,
    judge_scores,
    parse_judgement,
)
from contexts.activities.domain.models import (
    ActivitySubmission,
    ActivityType,
    SubmissionForJudging,
    ValidationResult,
    ValidatorKind,
)
from contexts.activities.interfaces.broadcast import dispatch_dashboard_submission_validated

# TTL after which a still-``pending`` async validation is swept to ``error`` — the
# single safety net for a stalled worker OR a dropped post-commit enqueue.
_PENDING_TTL_SECONDS = 900

# Covers the router's 60 s quota wait plus the default retry policy (4 attempts
# at a 120 s HTTP timeout, with backoff) and stays under the TTL. A group with a
# longer policy is cut off here; the cancel path still commits its usage rows.
JUDGE_JOB_TIMEOUT_S = 660

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
        await dispatch_dashboard_submission_validated(chatroom_id)
    if chatroom_id is not None and result_status == "validated":
        # [R15.28]: an invalid verdict counts for no one, so the check finds
        # nothing new; running it unconditionally keeps one decision point.
        from app.wiring.platform_wakes import check_all_submitted

        await check_all_submitted(chatroom_id)
    await _emit_activity_signal(signal_payload)
    return result_status


class _JudgeFailed(Exception):
    """One judge call ended without scores; ``reason`` is a closed vocabulary."""

    def __init__(self, reason: str, status: JudgeStatus = JudgeStatus.ERROR) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


async def _call_judge(db: Any, item: SubmissionForJudging) -> tuple[dict[str, Any], str]:
    """``(stored scores, model)`` for one submission, or :class:`_JudgeFailed`.

    The call is billed to the judge agent's key group and recorded under
    ``usage_context="activity_judge"`` with the room attached, so the owner can
    see what scoring cost. Nothing the model writes leaves this function except
    through :func:`parse_judgement`.
    """
    from contexts.agents.interfaces.facade import AgentsFacade

    config = item.creativity_scoring
    if config is None:
        raise _JudgeFailed(JUDGE_MISCONFIGURED)
    agents = AgentsFacade(db)
    try:
        target = await resolve_agent(agents, judge_agent_id(config), item.project_id)
        # Built before the call so an oversized answer is skipped without billing.
        try:
            system, user = build_judge_prompt(
                rubric=config["rubric"], payload=item.payload, payload_schema=item.payload_schema
            )
        except JudgeAnswerTooLarge as exc:
            raise _JudgeFailed(SKIP_ANSWER_TOO_LARGE, JudgeStatus.SKIPPED) from exc
        text = await call_agent_once(
            db,
            agents,
            target,
            system=system,
            user=user,
            max_tokens=MAX_OUTPUT_TOKENS,
            usage_context="activity_judge",
            log={"submission_id": str(item.id)},
            chatroom_id=item.chatroom_id,
            # Forwarded only to models that accept sampling parameters.
            extra={"temperature": 0},
        )
    except AgentCallFailed as exc:
        raise _JudgeFailed(
            JUDGE_MISCONFIGURED if exc.failure is AgentCallFailure.MISCONFIGURED else JUDGE_UNAVAILABLE
        ) from exc
    provider, model = target.provider, target.model
    try:
        parsed = parse_judgement(text)
    except JudgeOutputInvalid as exc:
        raise _JudgeFailed(JUDGE_BAD_OUTPUT) from exc
    scores = judge_scores(
        parsed=parsed,
        fluency_count=fluency(item.payload, item.payload_schema),
        provider=provider.value,
        model=model,
    )
    return scores, model


async def _record_judge_timeout(db: Any, sid: uuid.UUID) -> None:
    from contexts.activities.interfaces.facade import ActivitiesFacade

    try:
        await ActivitiesFacade(db).record_judgement(
            submission_id=sid, status=JudgeStatus.ERROR, scores=None, reason=JUDGE_TIMEOUT, model=None
        )
        await db.commit()
    except Exception as exc:
        # The watchdog still sweeps the row; only the usage rows are lost.
        logger.bind(submission_id=str(sid)).warning(f"judge timeout write-back failed: {type(exc).__name__}")


async def score_activity_submission(ctx: dict[str, Any], submission_id: str) -> str:
    """Score one submission with the type's judge agent and write the result back.

    Single attempt (``max_tries=1``): a retry would bill the owner's key again for
    a reply that was already received or already refused. A job that never runs is
    caught by the watchdog's ``judge_timeout`` sweep. The result goes to the
    dashboard only: the room never hears about scoring (spec section 7, AC-17).
    """
    _ = ctx
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from contexts.conversation.interfaces.facade import ConversationFacade
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    sid = uuid.UUID(str(submission_id))
    async with async_session() as db:
        facade = ActivitiesFacade(db)
        item = await facade.get_submission_for_judging(sid)
        if item is None:
            return "missing"
        if item.judge_status != JudgeStatus.PENDING.value:
            return "not-pending"

        scores: dict[str, Any] | None = None
        reason: str | None = None
        model: str | None = None
        if item.creativity_scoring is not None and not await ConversationFacade(db).is_agent_in_chatroom(
            chatroom_id=item.chatroom_id, agent_id=judge_agent_id(item.creativity_scoring)
        ):
            # The owner chose a judge but did not bring it into this room; the
            # room's members never agreed to that agent reading their answers.
            status, reason = JudgeStatus.SKIPPED, SKIP_JUDGE_NOT_BOUND
        else:
            try:
                scores, model = await _call_judge(db, item)
                status = JudgeStatus.SCORED
            except _JudgeFailed as exc:
                status, reason = exc.status, exc.reason
            except asyncio.CancelledError:
                # arq enforces the timeout by cancelling. Without this the session
                # rolls back the usage rows of calls the provider already billed.
                # Shielded so the write lands; re-raised to keep arq's contract.
                logger.bind(submission_id=str(sid)).warning("activity judge timed out")
                await asyncio.shield(_record_judge_timeout(db, sid))
                raise
            except Exception as exc:
                # A lookup before the provider call failed; the class only, as above.
                logger.bind(submission_id=str(sid)).warning(f"judge setup failed: {type(exc).__name__}")
                status, reason = JudgeStatus.ERROR, JUDGE_UNAVAILABLE
        changed = await facade.record_judgement(
            submission_id=sid, status=status, scores=scores, reason=reason, model=model
        )
        # Commit even when nothing changed: the router has recorded the usage row.
        await db.commit()
        await flush_tail_events(db)

    if not changed:
        return "not-pending"
    logger.bind(submission_id=str(sid), judge_status=status.value, reason=reason).info("activity judged")
    await dispatch_dashboard_submission_validated(item.chatroom_id)
    return status.value


async def check_all_submitted(ctx: dict[str, Any], chatroom_id: str) -> str:
    """[R15.28]: the all-submitted check a participant's departure deferred past the
    reconnect grace (``platform_wakes.schedule_leave_recheck``). Idempotent: the
    completion is claimed once per activation, so a redelivery or a second leaver's
    job finds nothing new."""
    _ = ctx
    from app.wiring import platform_wakes

    await platform_wakes.check_all_submitted(uuid.UUID(chatroom_id))
    return "checked"


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
    to_emit: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any] | None, bool]] = []
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
            signal_built = True
            try:
                payload = await facade.build_activity_signal(submission_id=submission_id)
            except Exception:
                logger.bind(submission_id=str(submission_id)).warning(
                    "activity watchdog signal build failed", exc_info=True
                )
                payload = None
                signal_built = False
            to_emit.append((chatroom_id, submission_id, payload, signal_built))

    # Both emits already swallow their own failures, so one bad row cannot fail the
    # sweep or block the rest.
    for chatroom_id, submission_id, payload, signal_built in to_emit:
        await dispatch_dashboard_submission_validated(chatroom_id)
        if signal_built:
            await _emit_validated(chatroom_id, submission_id, "error")
            await _emit_activity_signal(payload)

    judged_swept = await _sweep_stalled_judging()

    swept_count = len(swept)
    logger.bind(event="activities_watchdog_done", swept=swept_count, judge_swept=judged_swept).info(
        f"activities watchdog: swept {swept_count} stalled submissions, {judged_swept} stalled judgements"
    )
    return f"swept={swept_count} judge_swept={judged_swept}"


async def _sweep_stalled_judging() -> int:
    """Move scoring still ``pending`` past the TTL to ``error`` / ``judge_timeout``.

    Its own transaction, so a failure here cannot undo the validation sweep. It
    invalidates the dashboard and nothing else: scoring has no room event and no
    workflow signal to replay (spec section 7).
    """
    from contexts.activities.interfaces.facade import ActivitiesFacade
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        swept = await ActivitiesFacade(db).sweep_stalled_judging(ttl_seconds=_PENDING_TTL_SECONDS)
        await db.commit()
        await flush_tail_events(db)
    for chatroom_id in {room for _sid, room in swept}:
        await dispatch_dashboard_submission_validated(chatroom_id)
    return len(swept)


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


__all__ = [
    "activities_watchdog",
    "expire_group_proposals",
    "score_activity_submission",
    "validate_activity_submission",
]
