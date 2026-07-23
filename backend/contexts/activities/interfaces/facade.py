"""Activities facade — the public surface for routes, the validation worker, and
the observer context provider.

Thin pass-throughs to the application services (caller owns commit). This is the
ONLY way other layers touch the activities context; the context itself imports
only the conversation facade + shared_kernel (never the agents context).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.activation_service import ActivationService
from contexts.activities.application.aggregation_service import AggregationService
from contexts.activities.application.session_service import ActivitySessionService
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import (
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityAggregate,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    RecentActivityRow,
    ValidationResult,
    ValidatorKind,
)
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository

__all__ = [
    "ActivitiesFacade",
    "ActivityAggregate",
    "ActivityActivation",
    "ActivityActivationEndResult",
    "ActivitySession",
    "ActivitySubmission",
    "ActivityType",
    "RecentActivityRow",
    "ValidationResult",
    "ValidatorKind",
]


class ActivitiesFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._types = ActivityTypeService(db)
        activation_repo = ActivationRepository(db)
        self._activation_repo = activation_repo
        self._activation = ActivationService(
            db,
            activation_repo=activation_repo,
            type_repo=ActivityTypeRepository(db),
        )
        self._sessions = ActivitySessionService(db)
        self._submissions = SubmissionService(db, activation_repo=activation_repo)
        self._aggregation = AggregationService(db)

    # -- Types --------------------------------------------------------------

    async def register_type(
        self,
        *,
        project_id: uuid.UUID,
        key: str,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        return await self._types.register(
            project_id=project_id,
            key=key,
            name=name,
            payload_schema=payload_schema,
            validator_kind=validator_kind,
            validator_config=validator_config,
            retention_days=retention_days,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def update_type(
        self,
        *,
        project_id: uuid.UUID,
        type_id: uuid.UUID,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        return await self._types.update(
            project_id=project_id,
            type_id=type_id,
            name=name,
            payload_schema=payload_schema,
            validator_kind=validator_kind,
            validator_config=validator_config,
            retention_days=retention_days,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_types(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        return await self._types.list_types(project_id)

    async def get_type(self, type_id: uuid.UUID) -> ActivityType | None:
        return await self._types.get_type(type_id)

    # -- Activations --------------------------------------------------------

    async def start_activation(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        started_by_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityActivation:
        return await self._activation.start(
            project_id=project_id,
            chatroom_id=chatroom_id,
            activity_type_id=activity_type_id,
            started_by_user_id=started_by_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def end_activation(
        self,
        *,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityActivationEndResult:
        return await self._activation.end(
            chatroom_id=chatroom_id,
            activation_id=activation_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def get_active_activation(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        return await self._activation.get_active(chatroom_id)

    async def delete_type(
        self,
        *,
        project_id: uuid.UUID,
        type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Cascade-delete a type: end every active activation referencing it, then
        soft-delete the type, all on the caller's transaction (R30.23).

        Returns the ``(chatroom_id, activation_id)`` pairs actually transitioned so
        the route can emit ``activity.activation.ended`` per room post-commit. The
        ``project_id`` guard makes this tenant-safe: an owner of one project cannot
        delete another project's type via a mismatched path.
        """
        existing = await self._types.get_type(type_id)
        if existing is None or existing.project_id != project_id:
            raise ActivityTypeNotFound(str(type_id))

        ended: list[tuple[uuid.UUID, uuid.UUID]] = []
        for activation in await self._activation_repo.list_active_for_type(type_id):
            result = await self._activation.end(
                chatroom_id=activation.chatroom_id,
                activation_id=activation.id,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            if result.transitioned:
                ended.append((activation.chatroom_id, activation.id))

        # Force-close any in-flight session so none outlives its type (FU-4).
        await self._sessions.close_open_for_type(type_id)

        await self._types.soft_delete(
            type_id=type_id, actor_user_id=actor_user_id, actor_ip=actor_ip, request_id=request_id
        )
        return ended

    # -- Sessions -----------------------------------------------------------

    async def open_session(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
    ) -> ActivitySession:
        return await self._sessions.open_session(
            project_id=project_id,
            activity_type_id=activity_type_id,
            chatroom_id=chatroom_id,
            subject_user_id=subject_user_id,
            caller_user_id=caller_user_id,
        )

    async def close_session(
        self,
        *,
        session_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._sessions.close_session(
            session_id=session_id,
            chatroom_id=chatroom_id,
            subject_user_id=subject_user_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def get_session(self, session_id: uuid.UUID) -> ActivitySession | None:
        return await self._sessions.get_session(session_id)

    # -- Submissions --------------------------------------------------------

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
        """Returns the persisted submission and the pre-built reactive-rules signal
        payload (R30.12); the route enqueues the payload best-effort post-commit."""
        return await self._submissions.submit(
            project_id=project_id,
            activity_type_id=activity_type_id,
            chatroom_id=chatroom_id,
            producer_user_id=producer_user_id,
            subject_user_id=subject_user_id,
            caller_user_id=caller_user_id,
            payload=payload,
            session_id=session_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def get_submission(self, submission_id: uuid.UUID) -> ActivitySubmission | None:
        return await self._submissions.get_submission(submission_id)

    async def build_activity_signal(self, *, submission_id: uuid.UUID) -> dict[str, Any] | None:
        """Assemble the reactive-rules ``workflow_signal("activity", …)`` payload
        for a submission (R30.12); returns None if the submission is gone. Callers
        (route post-commit, validation worker) enqueue the result best-effort."""
        return await self._submissions.build_activity_signal(submission_id=submission_id)

    async def record_validation(
        self, *, submission_id: uuid.UUID, result: ValidationResult, latency_ms: int | None
    ) -> bool:
        return await self._submissions.record_validation(
            submission_id=submission_id, result=result, latency_ms=latency_ms
        )

    async def record_validation_error(self, *, submission_id: uuid.UUID, error_class: str) -> bool:
        return await self._submissions.record_validation_error(
            submission_id=submission_id, error_class=error_class
        )

    async def sweep_stalled(
        self, *, ttl_seconds: int, error_class: str = "validation_timeout"
    ) -> Sequence[tuple[uuid.UUID, uuid.UUID]]:
        return await self._submissions.sweep_stalled(ttl_seconds=ttl_seconds, error_class=error_class)

    # -- Read model ---------------------------------------------------------

    async def list_submissions(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[ActivitySubmission]:
        return await self._aggregation.list_submissions(
            chatroom_id=chatroom_id,
            session_id=session_id,
            subject_user_id=subject_user_id,
            limit=limit,
            offset=offset,
        )

    async def aggregate(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
    ) -> ActivityAggregate:
        return await self._aggregation.aggregate(
            chatroom_id=chatroom_id, session_id=session_id, subject_user_id=subject_user_id
        )

    async def list_recent_activity(self, chatroom_id: uuid.UUID, limit: int) -> Sequence[RecentActivityRow]:
        """Bounded, most-recent-first activity for the observer context provider
        (R30.10). Positional signature matches the observer dossier's call."""
        return await self._aggregation.list_recent_activity(chatroom_id=chatroom_id, limit=limit)
