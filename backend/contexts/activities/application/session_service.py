"""Activity-session lifecycle (Chapter §30, R30.01, R30.22, §5.4).

A session belongs to one ``ActivityActivation`` (0077), so every entry point here
resolves the round first and keys on ``(activation, subject)``. Open is idempotent
under that unique: an existing session for the round is returned rather than
duplicated; a lost lazy-open race re-selects the winner. Caller owns commit.

Participants no longer open or close their own sessions from the UI ([R30.22]) --
the first submission opens one and the facilitator's end closes it. What a
participant does own is :meth:`ActivitySessionService.set_completion`, the
reversible "I am finished" declaration, which is stored separately from ``status``
precisely so the facilitator's end cannot be mistaken for the class finishing.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.ports import ActivityActivationRepository
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.domain.errors import (
    ActivityActivationNotFound,
    ActivityNotActive,
    SessionNotFound,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivitySession,
    ActivitySessionCompletionResult,
)
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from shared_kernel import audit


def _ensure_subject_is_caller(subject_user_id: uuid.UUID, caller_user_id: uuid.UUID | None) -> None:
    """A session is a per-subject resource, so a caller may only act on their own
    subject. ``caller_user_id`` is ``None`` for the platform-admin arm (no subject
    constraint). A mismatch collapses into ``SessionNotFound`` (Q-1): the same 404
    the room/existence checks already return, which also declines to confirm the
    resource to a non-subject."""
    if caller_user_id is not None and subject_user_id != caller_user_id:
        raise SessionNotFound(str(subject_user_id))


class ActivitySessionService:
    def __init__(
        self, db: AsyncSession, *, activation_repo: ActivityActivationRepository | None = None
    ) -> None:
        self._db = db
        self._repo = ActivitySessionRepository(db)
        self._type_repo = ActivityTypeRepository(db)
        self._optin_repo = ProjectActivityTypeOptInRepository(db)
        # Defaulted from the session so the facade's construction stays a one-liner
        # and a test double supplying only what it exercises keeps working -- the
        # same shape ``ActivationService`` uses for its opt-in repo.
        self._activation_repo = activation_repo or ActivationRepository(db)

    async def open_session(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
    ) -> ActivitySession:
        """Return this subject's session for the room's live round, opening one if
        none exists. At most one can exist per the (activation, subject) unique, so
        a concurrent open resolves to the same row. ``caller_user_id`` is ``None``
        for the admin arm; otherwise it must equal ``subject_user_id``."""
        # Tenant isolation (mirrors SubmissionService.submit): the type must be
        # reachable from the room's project -- its own, or a platform type the
        # project opted into ([R30.33]). Anything else -> NotFound, so a room
        # member can never open a session against another tenant's type. Ordered
        # before the subject check so a cross-tenant probe still gets 404 on the
        # type rather than a subject-mismatch answer that confirms it exists.
        await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activity_type_id,
            project_id=project_id,
        )
        # A session with no round is the hole this endpoint used to leave open: it
        # could never receive a submission ([R30.22] needs an active activation)
        # yet it was indistinguishable from a live one and the next round would
        # adopt it. Ordered after the type gate so a cross-tenant probe cannot
        # learn another room's activation state.
        activation = await self._activation_repo.get_active(chatroom_id)
        if activation is None or activation.activity_type_id != activity_type_id:
            raise ActivityNotActive(str(activity_type_id))
        _ensure_subject_is_caller(subject_user_id, caller_user_id)

        return await self._resolve_for_activation(activation=activation, subject_user_id=subject_user_id)

    async def set_completion(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
        completed: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivitySessionCompletionResult:
        """Set or clear this subject's "I am finished" declaration ([R30.22]).

        Returns the session, the round it belongs to, and whether this call
        changed anything, so the route can address its broadcast and skip it on a
        repeat. Reversible by design: an accidental click must not lock a
        participant out of the rest of the lesson, and the declaration never
        gates submission -- a later submit clears it
        (``SubmissionService.submit``).
        """
        activation = await self._resolve_active_activation(
            project_id=project_id, chatroom_id=chatroom_id, activation_id=activation_id
        )
        _ensure_subject_is_caller(subject_user_id, caller_user_id)

        session = await self._resolve_for_activation(activation=activation, subject_user_id=subject_user_id)
        transitioned = await self._repo.set_completed(session.id, completed=completed)
        if transitioned:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action=(
                        "activity.session_completed" if completed else "activity.session_completion_cleared"
                    ),
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="activity_session",
                    resource_id=session.id,
                    metadata={
                        "session_id": str(session.id),
                        "chatroom_id": str(chatroom_id),
                        "activation_id": str(activation.id),
                        "subject_user_id": str(subject_user_id),
                    },
                    request_id=request_id,
                ),
            )
            refreshed = await self._repo.get(session.id)
            if refreshed is not None:
                return ActivitySessionCompletionResult(
                    session=refreshed, activation=activation, transitioned=True
                )
        return ActivitySessionCompletionResult(
            session=session, activation=activation, transitioned=transitioned
        )

    async def get_for_round(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
    ) -> ActivitySession | None:
        """This subject's session for one round, or ``None`` if they have none.

        The read counterpart of :meth:`set_completion`, and the only way a
        reloading participant can learn they had already declared themselves
        finished -- their client holds no session id to ask with. Creates
        nothing: opening a session is what a submission does, and a participant
        merely looking at the panel has not answered anything.

        Unlike ``set_completion`` this does not require the round to still be
        running: reading back what you declared during a round the facilitator
        has just ended is harmless, and refusing it would blank the surface at
        exactly the moment it is being torn down anyway.
        """
        activation = await self._resolve_activation(
            project_id=project_id, chatroom_id=chatroom_id, activation_id=activation_id
        )
        _ensure_subject_is_caller(subject_user_id, caller_user_id)
        return await self._repo.get_for_activation(
            activation_id=activation.id, subject_user_id=subject_user_id
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
        """Close a session. ``subject_user_id`` is the caller's subject constraint
        (``None`` for the admin arm); a session belonging to a different subject
        returns the same ``SessionNotFound`` (404) as a missing/wrong-room session
        (Q-1). A real close (not a double-close no-op) is audited in-transaction."""
        session = await self._repo.get(session_id)
        if (
            session is None
            or session.chatroom_id != chatroom_id
            or (subject_user_id is not None and session.subject_user_id != subject_user_id)
        ):
            raise SessionNotFound(str(session_id))
        if await self._repo.close(session_id):
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.session_closed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="activity_session",
                    resource_id=session_id,
                    metadata={
                        "session_id": str(session_id),
                        "chatroom_id": str(chatroom_id),
                        "subject_user_id": str(session.subject_user_id),
                    },
                    request_id=request_id,
                ),
            )

    async def get_session(self, session_id: uuid.UUID) -> ActivitySession | None:
        return await self._repo.get(session_id)

    async def count_for_activation(
        self, *, chatroom_id: uuid.UUID, activation_id: uuid.UUID
    ) -> tuple[int, int]:
        """``(completed, in_progress)`` for one round, for the facilitator's
        progress read. The room guard is here rather than at the route so the
        activation id cannot be used to read another room's progress."""
        activation = await self._activation_repo.get(activation_id)
        if activation is None or activation.chatroom_id != chatroom_id:
            raise ActivityActivationNotFound(str(activation_id))
        return await self._repo.count_for_activation(activation_id)

    async def close_open_for_activation(self, activation_id: uuid.UUID) -> int:
        """Close every open session of one round (the facilitator's end cascade)."""
        return await self._repo.close_open_for_activation(activation_id)

    async def close_open_for_type(self, activity_type_id: uuid.UUID) -> int:
        """Close every open session for a type (type-deletion cascade).

        Still distinct from :meth:`close_open_for_activation` after 0077: this one
        answers "the type is going away", which is also the only sweep that reaches
        pre-0077 rows carrying no ``activation_id``.
        """
        return await self._repo.close_open_for_type(activity_type_id)

    async def _resolve_activation(
        self, *, project_id: uuid.UUID, chatroom_id: uuid.UUID, activation_id: uuid.UUID
    ) -> ActivityActivation:
        """The named activation, proven to be this room's and this project's.

        A 404 for the wrong room: the id came from the client, and an activation
        in someone else's room is not this caller's to confirm.
        """
        activation = await self._activation_repo.get(activation_id)
        if activation is None or activation.chatroom_id != chatroom_id:
            raise ActivityActivationNotFound(str(activation_id))
        # Tenant isolation on the type, same gate as every other room-level path
        # ([R30.33]) -- the activation row alone does not prove the project may
        # still use the type it names.
        await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activation.activity_type_id,
            project_id=project_id,
        )
        return activation

    async def _resolve_active_activation(
        self, *, project_id: uuid.UUID, chatroom_id: uuid.UUID, activation_id: uuid.UUID
    ) -> ActivityActivation:
        """As :meth:`_resolve_activation`, and still running.

        A 409 once it has ended rather than a 404: the request was legal when the
        client rendered it, and what changed is the room's state.
        """
        activation = await self._resolve_activation(
            project_id=project_id, chatroom_id=chatroom_id, activation_id=activation_id
        )
        if activation.status is not ActivationStatus.ACTIVE:
            raise ActivityNotActive(str(activation.activity_type_id))
        return activation

    async def _resolve_for_activation(
        self, *, activation: ActivityActivation, subject_user_id: uuid.UUID
    ) -> ActivitySession:
        """This subject's session for the round, opening one if none exists."""
        existing = await self._repo.get_for_activation(
            activation_id=activation.id, subject_user_id=subject_user_id
        )
        if existing is not None:
            return existing
        session_id = await self._repo.create_open(
            activity_type_id=activation.activity_type_id,
            chatroom_id=activation.chatroom_id,
            subject_user_id=subject_user_id,
            activation_id=activation.id,
        )
        if session_id is not None:
            opened = await self._repo.get(session_id)
            if opened is not None:
                return opened
        # Lost the lazy-open race -- re-select the winner.
        winner = await self._repo.get_for_activation(
            activation_id=activation.id, subject_user_id=subject_user_id
        )
        if winner is None:  # pragma: no cover -- a winner must exist post-conflict
            raise SessionNotFound("could not open or resolve a session")
        return winner


__all__ = ["ActivitySessionService"]
