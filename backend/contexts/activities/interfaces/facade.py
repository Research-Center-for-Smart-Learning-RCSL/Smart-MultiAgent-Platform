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
from contexts.activities.application.example_service import (
    ActivityExampleService,
    CatalogueEntry,
    InstallReport,
    PlatformExample,
)
from contexts.activities.application.policy_service import ActivityPolicyService
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.application.session_service import ActivitySessionService
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.application.validators.registry import ValidatorInfo, list_registered
from contexts.activities.domain.errors import (
    ActivityTypeNotFound,
    ActivityTypeViolatesPolicy,
    PlatformActivityTypeReadOnly,
)
from contexts.activities.domain.models import (
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityAggregate,
    ActivityPolicy,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ActivityTypeScope,
    PolicyImpact,
    RecentActivityRow,
    ValidationResult,
    ValidatorKind,
)
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository

# Upper bound on the admin form's "how many types would this break" preview. A
# platform with more live types than this gets an undercount, which the API
# reports as approximate rather than silently truncating.
_POLICY_PREVIEW_SCAN = 500

__all__ = [
    "ActivitiesFacade",
    "ActivityActivation",
    "ActivityActivationEndResult",
    "ActivityAggregate",
    "ActivitySession",
    "ActivitySubmission",
    "ActivityType",
    "ActivityTypeScope",
    "CatalogueEntry",
    "InstallReport",
    "PlatformExample",
    "RecentActivityRow",
    "ValidationResult",
    "ValidatorInfo",
    "ValidatorKind",
]


class ActivitiesFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._types = ActivityTypeService(db)
        activation_repo = ActivationRepository(db)
        self._activation_repo = activation_repo
        self._type_repo = ActivityTypeRepository(db)
        self._optin_repo = ProjectActivityTypeOptInRepository(db)
        self._policy = ActivityPolicyService(db)
        self._activation = ActivationService(
            db,
            activation_repo=activation_repo,
            type_repo=self._type_repo,
            optin_repo=self._optin_repo,
        )
        self._sessions = ActivitySessionService(db)
        self._submissions = SubmissionService(db, activation_repo=activation_repo)
        self._aggregation = AggregationService(db)
        self._examples = ActivityExampleService(db)

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
        expose_payload_to_agent: bool = True,
        echo_includes_content: bool = False,
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
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
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
        expose_payload_to_agent: bool = True,
        echo_includes_content: bool = False,
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
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def update_platform_type(
        self,
        *,
        type_id: uuid.UUID,
        name: str,
        retention_days: int | None,
        expose_payload_to_agent: bool,
        echo_includes_content: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityType:
        """Admin edit of an installed example's safe + governance fields ([R30.23])."""
        return await self._types.update_platform_type(
            type_id=type_id,
            name=name,
            retention_days=retention_days,
            expose_payload_to_agent=expose_payload_to_agent,
            echo_includes_content=echo_includes_content,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_platform_types(self) -> Sequence[ActivityType]:
        """Every installed platform-scoped type ([R30.32])."""
        return await self._types.list_platform_types()

    @staticmethod
    def list_validators() -> list[ValidatorInfo]:
        """The process-global first-party in-process validators (id + title). No DB
        access: the registry is populated at startup (R30.05/R30.24)."""
        return list_registered()

    # -- Governance policy ([R30.29]) ---------------------------------------

    async def get_activity_policy(self) -> ActivityPolicy:
        """The policy in force; permissive when no admin has saved one."""
        return await self._policy.get_effective()

    async def update_activity_policy(
        self,
        *,
        expose_payload_to_agent_default: bool,
        expose_payload_to_agent_locked: bool,
        echo_includes_content_default: bool,
        echo_includes_content_locked: bool,
        retention_days_default: int | None,
        retention_days_max: int | None,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityPolicy:
        return await self._policy.update(
            expose_payload_to_agent_default=expose_payload_to_agent_default,
            expose_payload_to_agent_locked=expose_payload_to_agent_locked,
            echo_includes_content_default=echo_includes_content_default,
            echo_includes_content_locked=echo_includes_content_locked,
            retention_days_default=retention_days_default,
            retention_days_max=retention_days_max,
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def preview_policy_impact(self, policy: ActivityPolicy) -> PolicyImpact:
        """What a candidate policy would block: live types, and rooms running now.

        Read-only preview for the admin form: tightening a policy is otherwise a
        blind action whose cost only surfaces when a facilitator cannot start a
        class ([R30.30] risk note in §10 of the dossier).

        Two scans, each bounded — the bound stays inside the context so callers
        cannot drift from it, and `approximate` reports when either was hit.

        Rejects a self-contradictory candidate for the same reason `update` does.
        Reporting "nothing would break" on a policy the writer will refuse reads
        as approval of it.
        """
        ActivityPolicyService.assert_self_consistent(
            retention_days_default=policy.retention_days_default,
            retention_days_max=policy.retention_days_max,
        )

        async def _breaches(at: ActivityType | None) -> bool:
            if at is None:
                return False
            try:
                await self._policy.assert_allows(
                    expose_payload_to_agent=at.expose_payload_to_agent,
                    echo_includes_content=at.echo_includes_content,
                    retention_days=at.retention_days,
                    policy=policy,
                )
            except ActivityTypeViolatesPolicy:
                return True
            return False

        types = await self._type_repo.list_all(limit=_POLICY_PREVIEW_SCAN)
        violating_types = sum([1 for at in types if await _breaches(at)])

        # Counted separately from types, and from each activation's own type
        # rather than from the bounded type scan above, which may have truncated
        # before reaching it. An activity already running is the case an admin
        # most needs to see before saving: the two enforcement gates are
        # authoring and activation start, so a tightening does not end a class
        # that is already under way — it only stops the next one.
        activations = await self._activation_repo.list_all_active(limit=_POLICY_PREVIEW_SCAN)
        running_types = await self._type_repo.get_many([a.activity_type_id for a in activations])
        violating_activations = sum(
            [1 for a in activations if await _breaches(running_types.get(a.activity_type_id))]
        )

        return PolicyImpact(
            violating_types=violating_types,
            violating_activations=violating_activations,
            approximate=len(types) >= _POLICY_PREVIEW_SCAN or len(activations) >= _POLICY_PREVIEW_SCAN,
        )

    async def list_all_types(
        self, *, cursor: uuid.UUID | None = None, limit: int = 50
    ) -> Sequence[ActivityType]:
        """Live types across every project, for the admin governance view ([R30.31]).

        Deliberately tenant-unscoped: the only caller is the admin-gated route. It
        returns domain models, so the caller resolves project names through the
        tenancy facade rather than joining ([R30.09]).
        """
        return await self._type_repo.list_all(cursor=cursor, limit=limit)

    async def list_all_active_activations(
        self, *, cursor: uuid.UUID | None = None, limit: int = 50
    ) -> Sequence[ActivityActivation]:
        """Every ACTIVE activation across every room, for the admin view ([R30.31])."""
        return await self._activation_repo.list_all_active(cursor=cursor, limit=limit)

    async def get_types_by_ids(self, type_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ActivityType]:
        """Batch-resolve types by id, keyed by id, so an activation listing can name
        its type without a per-row query or a cross-table join."""
        return await self._type_repo.get_many(type_ids)

    async def list_types(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        return await self._types.list_types(project_id)

    async def get_type(self, type_id: uuid.UUID) -> ActivityType | None:
        """An unscoped read by id. Callers holding a project context must use
        :meth:`resolve_type_for_project` instead — this one answers "does the row
        exist", not "may this project see it"."""
        return await self._types.get_type(type_id)

    async def resolve_type_for_project(
        self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID
    ) -> ActivityType:
        """The type a project may use, or ``ActivityTypeNotFound`` ([R30.33]).

        The route-facing face of ``application.reachability``: room-scoped reads
        take a type id from the client just as the write paths do, so they need the
        same gate rather than a ``project_id`` comparison that a platform row (with
        no project) can never satisfy.
        """
        return await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activity_type_id,
            project_id=project_id,
        )

    # -- Shipped examples ([R30.32], [R30.33]) ------------------------------

    async def list_example_catalogue(self) -> tuple[CatalogueEntry, ...]:
        """Every shipped course with its install state (admin surface)."""
        return await self._examples.list_catalogue()

    async def install_example_course(
        self,
        *,
        course_key: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> InstallReport:
        return await self._examples.install_course(
            course_key=course_key,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_platform_examples_for_project(self, project_id: uuid.UUID) -> tuple[PlatformExample, ...]:
        """Installed platform types plus this project's enabled state."""
        return await self._examples.list_for_project(project_id)

    async def opt_project_in(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._examples.opt_in(
            project_id=project_id,
            activity_type_id=activity_type_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def opt_project_out(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Revoke access, ending only this project's activations ([R30.33])."""
        return await self._examples.opt_out(
            project_id=project_id,
            activity_type_id=activity_type_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

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
        """Cascade-delete a *project's* type: end every active activation
        referencing it, then soft-delete the type, all on the caller's transaction
        (R30.23).

        Returns the ``(chatroom_id, activation_id)`` pairs actually transitioned so
        the route can emit ``activity.activation.ended`` per room post-commit. The
        ``project_id`` guard makes this tenant-safe: an owner of one project cannot
        delete another project's type via a mismatched path.

        A platform-scoped type is refused with 403 ([R30.23]) — and the refusal is
        what keeps the unbounded cascade below correct. For a project-scoped type
        the id alone bounds the activation set to one project; for a platform type
        it would span every tenant that opted in, which is an admin's act
        (:meth:`delete_platform_type`), not a project owner's.
        """
        existing = await self._types.get_type(type_id)
        if existing is None:
            raise ActivityTypeNotFound(str(type_id))
        if existing.scope is ActivityTypeScope.PLATFORM:
            raise PlatformActivityTypeReadOnly(str(type_id))
        if existing.project_id != project_id:
            raise ActivityTypeNotFound(str(type_id))

        return await self._cascade_delete(
            type_id=type_id, actor_user_id=actor_user_id, actor_ip=actor_ip, request_id=request_id
        )

    async def delete_platform_type(
        self,
        *,
        type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Admin-only removal of an installed example ([R30.31], [R30.32]).

        The cascade legitimately spans every tenant here: the type is going away
        for everyone, so every active activation ends and every open session
        closes. Opting one project out is a *different* operation with a
        project-bounded cascade (:meth:`opt_out_project`) — they deliberately do
        not share a code path, because sharing one is exactly how a single
        project's revocation would end another project's class.

        The opt-in rows disappear with the type through the FK cascade, so no
        project is left holding an authorization for a row that no longer exists.
        """
        existing = await self._types.get_type(type_id)
        if existing is None or existing.scope is not ActivityTypeScope.PLATFORM:
            raise ActivityTypeNotFound(str(type_id))

        return await self._cascade_delete(
            type_id=type_id, actor_user_id=actor_user_id, actor_ip=actor_ip, request_id=request_id
        )

    async def _cascade_delete(
        self,
        *,
        type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """End every activation and close every open session for a type, then
        tombstone it. Unbounded across rooms — every caller must have established
        that the type is going away platform-wide or project-wide first."""
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
