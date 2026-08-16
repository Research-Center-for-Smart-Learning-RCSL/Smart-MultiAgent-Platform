"""Submission lifecycle: submit, validation write-back, watchdog sweep (§30).

``submit`` runs in the request transaction (the route commits once): resolve/open
the session and assign ``attempt_no`` under ``FOR UPDATE``, validate the payload
against the type schema, run the in-process validator synchronously (or persist
``pending`` for mcp/webhook), echo a SYSTEM message, and audit. WS emit + the
async enqueue are post-commit (route-side). ``record_validation`` /
``record_validation_error`` are the worker write-backs and are idempotent
(transition only from ``pending``). ``sweep_stalled`` is the watchdog.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.agent_digest import build_agent_digest
from contexts.activities.application.ports import ActivityActivationRepository
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.application.session_service import _ensure_subject_is_caller
from contexts.activities.application.validators.in_process import InProcessValidator
from contexts.activities.application.validators.schema import payload_errors
from contexts.activities.domain.errors import (
    ActivityNotActive,
    SessionNotFound,
    SubmissionNotFound,
    SubmissionPayloadInvalid,
)
from contexts.activities.domain.models import (
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ValidationResult,
    ValidationStatus,
    ValidatorKind,
)
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel import audit
from shared_kernel.auth.clients import now

_ECHO_TYPE = "activity_submission"
_MAX_ECHO_ERRORS = 5
_ROLLING_WINDOW_SECONDS = 60


class SubmissionService:
    def __init__(self, db: AsyncSession, *, activation_repo: ActivityActivationRepository) -> None:
        self._db = db
        self._type_repo = ActivityTypeRepository(db)
        self._optin_repo = ProjectActivityTypeOptInRepository(db)
        self._activation_repo = activation_repo
        self._session_repo = ActivitySessionRepository(db)
        self._sub_repo = ActivitySubmissionRepository(db)

    async def submit(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        producer_user_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
        payload: dict[str, object],
        session_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[ActivitySubmission, dict[str, Any]]:
        # Tenant isolation: the type must be reachable from the room's project —
        # its own, or a platform type the project opted into ([R30.33]). Missing,
        # cross-project, or not-opted-in all → NotFound (never leak another
        # tenant's type, and never confirm an example the project has not enabled).
        activity_type = await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activity_type_id,
            project_id=project_id,
        )
        # A submission writes into a per-subject attempt history, so a member may
        # only submit as themselves (caller_user_id=None is the admin arm). Ordered
        # AFTER the type isolation so a cross-tenant probe still 404s on the type.
        _ensure_subject_is_caller(subject_user_id, caller_user_id)

        # Hold the active row through the transaction. The facilitator's guarded
        # UPDATE must contend on this lock, so it cannot commit an end between
        # this check and the authoritative submission insert.
        activation = await self._activation_repo.get_active_for_update(chatroom_id)
        if activation is None or activation.activity_type_id != activity_type_id:
            raise ActivityNotActive(str(activity_type_id))

        errors = payload_errors(activity_type.payload_schema, dict(payload))
        if errors:
            raise SubmissionPayloadInvalid("; ".join(errors[:_MAX_ECHO_ERRORS]))

        session = await self._resolve_session(
            activity_type_id=activity_type_id,
            chatroom_id=chatroom_id,
            subject_user_id=subject_user_id,
            session_id=session_id,
        )
        # Serialize attempt numbering for concurrent submits to this session.
        locked = await self._session_repo.lock_for_update(session.id)
        if locked is None:  # pragma: no cover — resolved above in the same txn
            raise SessionNotFound(str(session.id))
        attempt_no = await self._sub_repo.next_attempt_no(session.id)

        created_ts = now()
        retain_until = (
            created_ts + timedelta(days=activity_type.retention_days)
            if activity_type.retention_days
            else None
        )

        latency_ms: int | None
        is_valid: bool | None
        error_class: str | None
        detail: str | None
        if activity_type.validator_kind is ValidatorKind.IN_PROCESS:
            start = time.monotonic()
            try:
                result = await InProcessValidator(self._db).validate(
                    activity_type=activity_type, payload=dict(payload)
                )
            except Exception:
                # A first-party scorer bug must not lose the participant's
                # submission: record it as ``error`` (mirrors the async path's
                # ValidatorUnavailable -> error) rather than 500-ing the request.
                # A DB error inside the scorer poisons the txn and still surfaces
                # on the insert below, which is the correct outcome for infra
                # failures.
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.bind(activity_type_id=str(activity_type_id)).warning(
                    "in-process validator raised; recording error verdict", exc_info=True
                )
                validation_status = ValidationStatus.ERROR
                is_valid = None
                error_class = "validator_error"
                sub_scores = {}
                validated_at = created_ts
                detail = None
            else:
                latency_ms = int((time.monotonic() - start) * 1000)
                validation_status = ValidationStatus.VALIDATED
                is_valid = result.is_valid
                error_class = result.error_class
                sub_scores = result.sub_scores
                validated_at = created_ts
                detail = result.detail
        else:
            latency_ms = None
            validation_status = ValidationStatus.PENDING
            is_valid = None
            error_class = None
            sub_scores = {}
            validated_at = None
            detail = None

        # Always computed (payload-JSON fallback when no validator ``detail`` is
        # available yet); the two ActivityType flags gate presentation per
        # channel, not this write (agent_digest.py docstring).
        agent_digest = build_agent_digest(payload=dict(payload), detail=detail)

        submission_id = await self._sub_repo.insert(
            session_id=session.id,
            activity_type_id=activity_type_id,
            chatroom_id=chatroom_id,
            producer_user_id=producer_user_id,
            payload=dict(payload),
            attempt_no=attempt_no,
            validation_status=validation_status,
            is_valid=is_valid,
            error_class=error_class,
            sub_scores=sub_scores,
            latency_ms=latency_ms,
            retain_until=retain_until,
            validated_at=validated_at,
            agent_digest=agent_digest,
        )

        # Adversarial-review fix: a chat message visible to human participants
        # is necessarily visible to every agent reading that same room
        # transcript (SYSTEM-sender rows flow into every agent's history the
        # same as any other message — there is no agent-invisible chat
        # channel). So echo_includes_content can only ever ADD the digest to
        # what expose_payload_to_agent already allows agents to see; it must
        # never show content the type owner turned off for agents by putting
        # it in the shared transcript instead. Both flags must be true.
        show_in_echo = activity_type.echo_includes_content and activity_type.expose_payload_to_agent
        echo_agent_digest = agent_digest if show_in_echo else None
        await ConversationFacade(self._db).insert_system_message(
            chatroom_id=chatroom_id,
            content_md=_echo_text(
                activity_type,
                attempt_no,
                validation_status,
                is_valid,
                error_class,
                echo_agent_digest,
            ),
            message_type=_ECHO_TYPE,
            metadata={
                "submission_id": str(submission_id),
                "activity_type_key": activity_type.key,
                "attempt_no": attempt_no,
            },
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity.submitted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_submission",
                resource_id=submission_id,
                metadata={
                    "activity_type_id": str(activity_type_id),
                    "chatroom_id": str(chatroom_id),
                    "session_id": str(session.id),
                    "attempt_no": attempt_no,
                    "validation_status": validation_status.value,
                },
                request_id=request_id,
            ),
        )

        submission = await self._sub_repo.get(submission_id)
        if submission is None:  # pragma: no cover — just inserted
            raise SubmissionNotFound(str(submission_id))
        # Build the reactive-rules signal here from objects already in hand
        # (type + session were resolved above) so the route does not re-fetch
        # them post-commit. The rolling count runs in this transaction and sees
        # the just-inserted row (a session reads its own writes).
        signal_payload = _assemble_activity_signal(
            submission=submission,
            activity_type_key=activity_type.key,
            activity_type_scope=activity_type.scope.value,
            subject_user_id=session.subject_user_id,
            same_error_count=await self._same_error_count(submission, _ROLLING_WINDOW_SECONDS),
            window_seconds=_ROLLING_WINDOW_SECONDS,
        )
        return submission, signal_payload

    async def record_validation(
        self,
        *,
        submission_id: uuid.UUID,
        result: ValidationResult,
        latency_ms: int | None,
    ) -> bool:
        """Worker write-back for a completed async verdict — idempotent.

        ``result.detail`` (when the mcp/webhook validator supplied one) replaces
        the submit-time payload-fallback ``agent_digest`` with the richer
        description; a capped copy is passed so a large remote payload can never
        grow the stored digest past the shared limit."""
        changed = await self._sub_repo.record_validation(
            submission_id=submission_id,
            is_valid=result.is_valid,
            error_class=result.error_class,
            sub_scores=result.sub_scores,
            latency_ms=latency_ms,
            validated_at=now(),
            agent_digest=build_agent_digest(payload={}, detail=result.detail) if result.detail else None,
        )
        if changed:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.validated",
                    resource_type="activity_submission",
                    resource_id=submission_id,
                    metadata={"validation_status": "validated", "is_valid": result.is_valid},
                ),
            )
        return changed

    async def record_validation_error(self, *, submission_id: uuid.UUID, error_class: str) -> bool:
        """Worker write-back when the validator could not run — idempotent."""
        changed = await self._sub_repo.record_error(
            submission_id=submission_id, error_class=error_class, validated_at=now()
        )
        if changed:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.validated",
                    resource_type="activity_submission",
                    resource_id=submission_id,
                    metadata={"validation_status": "error", "error_class": error_class},
                ),
            )
        return changed

    async def sweep_stalled(
        self, *, ttl_seconds: int, error_class: str = "validation_timeout"
    ) -> Sequence[tuple[uuid.UUID, uuid.UUID]]:
        """Watchdog: move ``pending`` submissions older than the TTL to ``error``,
        returning the ``(id, chatroom_id)`` of each swept row so the watchdog can
        notify per room (F-20)."""
        swept_at = now()
        cutoff = swept_at - timedelta(seconds=ttl_seconds)
        return await self._sub_repo.sweep_stalled(cutoff=cutoff, error_class=error_class, swept_at=swept_at)

    async def get_submission(self, submission_id: uuid.UUID) -> ActivitySubmission | None:
        return await self._sub_repo.get(submission_id)

    async def build_activity_signal(
        self, *, submission_id: uuid.UUID, window_seconds: int = _ROLLING_WINDOW_SECONDS
    ) -> dict[str, Any] | None:
        """Assemble the ``workflow_signal("activity", …)`` payload for a submission
        (R30.12) by re-reading it — used by the validation worker, whose in-memory
        row is the stale ``pending`` one. Reads the authoritative submission plus
        its type (for the matchable ``activity_type_key``) and session (for
        ``subject_user_id``). Returns ``None`` if the submission is gone; the caller
        enqueues best-effort. The route path does not use this — it reuses the
        payload ``submit`` already built from objects in hand.
        """
        submission = await self._sub_repo.get(submission_id)
        if submission is None:
            return None
        activity_type = await self._type_repo.get(submission.activity_type_id)
        session = await self._session_repo.get(submission.session_id)
        return _assemble_activity_signal(
            submission=submission,
            activity_type_key=activity_type.key if activity_type is not None else "",
            activity_type_scope=activity_type.scope.value if activity_type is not None else "",
            subject_user_id=session.subject_user_id if session is not None else None,
            same_error_count=await self._same_error_count(submission, window_seconds),
            window_seconds=window_seconds,
        )

    async def _same_error_count(self, submission: ActivitySubmission, window_seconds: int) -> int:
        """Count same-``error_class`` submissions in this session over the recent
        window; ``0`` (no query) when the submission has no ``error_class`` yet
        (still pending, or a valid answer)."""
        if submission.error_class is None:
            return 0
        since = now() - timedelta(seconds=window_seconds)
        return await self._sub_repo.count_recent_same_error(
            session_id=submission.session_id, error_class=submission.error_class, since=since
        )

    async def _resolve_session(
        self,
        *,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> ActivitySession:
        if session_id is not None:
            session = await self._session_repo.get(session_id)
            if (
                session is None
                or session.activity_type_id != activity_type_id
                or session.chatroom_id != chatroom_id
                or session.subject_user_id != subject_user_id
                or session.status.value != "open"
            ):
                raise SessionNotFound(str(session_id))
            return session

        existing = await self._session_repo.get_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if existing is not None:
            return existing
        new_id = await self._session_repo.create_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if new_id is not None:
            opened = await self._session_repo.get(new_id)
            if opened is not None:
                return opened
        winner = await self._session_repo.get_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if winner is None:  # pragma: no cover — a winner must exist post-conflict
            raise SessionNotFound("could not open or resolve a session")
        return winner


def _assemble_activity_signal(
    *,
    submission: ActivitySubmission,
    activity_type_key: str,
    activity_type_scope: str,
    subject_user_id: uuid.UUID | None,
    same_error_count: int,
    window_seconds: int,
) -> dict[str, object]:
    """Build the reactive-rules ``activity`` signal payload (R30.12).

    ``rolling`` is **always present and numeric** — ``same_error_count`` and
    ``latency_ms`` default to ``0`` — even on the pending submit emit, so a
    stateless SEL rule evaluating ``int({{ trigger.rolling.same_error_count }})``
    never dereferences ``None``. ``error_class``/``is_valid`` stay ``None`` while
    pending (they are compared, not coerced, so no crash). All fields derive from
    the authoritative row, never the client.

    ``activity_type_id`` and ``activity_type_scope`` follow the same
    always-present discipline (``""`` when the type row has gone, matching what
    ``activity_type_key`` already does). They exist because ``key`` alone no
    longer identifies one type: a project's usable set may hold its own type and
    an opted-in platform type under the same key ([R30.02]), and a rule matching
    on the key alone fires for both.
    """
    return {
        "submission_id": str(submission.id),
        "chatroom_id": str(submission.chatroom_id),
        "activity_type_key": activity_type_key,
        "activity_type_id": str(submission.activity_type_id),
        "activity_type_scope": activity_type_scope,
        "session_id": str(submission.session_id),
        "subject_user_id": str(subject_user_id) if subject_user_id is not None else None,
        "attempt_no": submission.attempt_no,
        "validation_status": submission.validation_status.value,
        "is_valid": submission.is_valid,
        "error_class": submission.error_class,
        "rolling": {
            "same_error_count": same_error_count,
            "window_seconds": window_seconds,
            "latency_ms": submission.latency_ms if submission.latency_ms is not None else 0,
        },
    }


def _echo_text(
    activity_type: ActivityType,
    attempt_no: int,
    status: ValidationStatus,
    is_valid: bool | None,
    error_class: str | None,
    agent_digest: str | None,
) -> str:
    """Neutral for pending; renders the deterministic outcome for in-process.

    ``agent_digest`` is the caller's *already-gated* value (``None`` unless the
    type's ``echo_includes_content`` opts in) — this function does not re-check
    the flag, it just appends what it is given."""
    if status is ValidationStatus.VALIDATED:
        if is_valid:
            outcome = "valid"
        elif error_class:
            outcome = f"invalid ({error_class})"
        else:
            outcome = "invalid"
        text = f"Submitted attempt #{attempt_no} to {activity_type.name}: {outcome}."
    else:
        text = f"Submitted attempt #{attempt_no} to {activity_type.name}."
    if agent_digest:
        text = f"{text}\nContent: {agent_digest}"
    return text


__all__ = ["SubmissionService"]
