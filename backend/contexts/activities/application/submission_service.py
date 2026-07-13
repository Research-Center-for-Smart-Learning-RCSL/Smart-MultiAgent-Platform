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
from datetime import timedelta
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.validators.in_process import InProcessValidator
from contexts.activities.application.validators.schema import payload_errors
from contexts.activities.domain.errors import (
    ActivityTypeNotFound,
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
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._type_repo = ActivityTypeRepository(db)
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
        payload: dict[str, object],
        session_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivitySubmission:
        activity_type = await self._type_repo.get(activity_type_id)
        # Tenant isolation: the type must live in the room's project. Missing or
        # cross-project → NotFound (never leak another tenant's type).
        if activity_type is None or activity_type.project_id != project_id:
            raise ActivityTypeNotFound(str(activity_type_id))

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
            else:
                latency_ms = int((time.monotonic() - start) * 1000)
                validation_status = ValidationStatus.VALIDATED
                is_valid = result.is_valid
                error_class = result.error_class
                sub_scores = result.sub_scores
                validated_at = created_ts
        else:
            latency_ms = None
            validation_status = ValidationStatus.PENDING
            is_valid = None
            error_class = None
            sub_scores = {}
            validated_at = None

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
        )

        await ConversationFacade(self._db).insert_system_message(
            chatroom_id=chatroom_id,
            content_md=_echo_text(activity_type, attempt_no, validation_status, is_valid, error_class),
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
        return submission

    async def record_validation(
        self,
        *,
        submission_id: uuid.UUID,
        result: ValidationResult,
        latency_ms: int | None,
    ) -> bool:
        """Worker write-back for a completed async verdict — idempotent."""
        changed = await self._sub_repo.record_validation(
            submission_id=submission_id,
            is_valid=result.is_valid,
            error_class=result.error_class,
            sub_scores=result.sub_scores,
            latency_ms=latency_ms,
            validated_at=now(),
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

    async def sweep_stalled(self, *, ttl_seconds: int, error_class: str = "validation_timeout") -> int:
        """Watchdog: move ``pending`` submissions older than the TTL to ``error``."""
        swept_at = now()
        cutoff = swept_at - timedelta(seconds=ttl_seconds)
        return await self._sub_repo.sweep_stalled(cutoff=cutoff, error_class=error_class, swept_at=swept_at)

    async def get_submission(self, submission_id: uuid.UUID) -> ActivitySubmission | None:
        return await self._sub_repo.get(submission_id)

    async def build_activity_signal(
        self, *, submission_id: uuid.UUID, window_seconds: int = _ROLLING_WINDOW_SECONDS
    ) -> dict[str, Any] | None:
        """Assemble the ``workflow_signal("activity", …)`` payload for a submission
        (R30.12). Reads the authoritative submission plus its type (for the
        matchable ``activity_type_key``) and session (for ``subject_user_id``).

        On a completion status (``validated``/``error``) it attaches a numeric
        rolling aggregate — ``same_error_count`` over a bounded recent window keyed
        by the submission's own non-null ``error_class`` — so a stateless SEL rule
        can gate on ``int(trigger.rolling.same_error_count) >= N``. A still-pending
        submission (async validator not yet run) carries no ``error_class``/rolling.
        Returns ``None`` if the submission is gone; the caller enqueues best-effort.
        """
        submission = await self._sub_repo.get(submission_id)
        if submission is None:
            return None
        activity_type = await self._type_repo.get(submission.activity_type_id)
        session = await self._session_repo.get(submission.session_id)
        payload: dict[str, Any] = {
            "chatroom_id": str(submission.chatroom_id),
            "activity_type_key": activity_type.key if activity_type is not None else "",
            "session_id": str(submission.session_id),
            "subject_user_id": str(session.subject_user_id) if session is not None else None,
            "attempt_no": submission.attempt_no,
            "validation_status": submission.validation_status.value,
            "is_valid": submission.is_valid,
            "error_class": submission.error_class,
        }
        if submission.validation_status is not ValidationStatus.PENDING:
            same_error_count = 0
            if submission.error_class is not None:
                since = now() - timedelta(seconds=window_seconds)
                same_error_count = await self._sub_repo.count_recent_same_error(
                    session_id=submission.session_id,
                    error_class=submission.error_class,
                    since=since,
                )
            payload["rolling"] = {
                "same_error_count": same_error_count,
                "window_seconds": window_seconds,
                "latency_ms": submission.latency_ms,
            }
        return payload

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


def _echo_text(
    activity_type: ActivityType,
    attempt_no: int,
    status: ValidationStatus,
    is_valid: bool | None,
    error_class: str | None,
) -> str:
    """Neutral for pending; renders the deterministic outcome for in-process."""
    if status is ValidationStatus.VALIDATED:
        if is_valid:
            outcome = "valid"
        elif error_class:
            outcome = f"invalid ({error_class})"
        else:
            outcome = "invalid"
        return f"Submitted attempt #{attempt_no} to {activity_type.name}: {outcome}."
    return f"Submitted attempt #{attempt_no} to {activity_type.name}."


__all__ = ["SubmissionService"]
