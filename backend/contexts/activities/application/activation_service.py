"""Facilitator-controlled room-level activity activation lifecycle."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.policy_service import ActivityPolicyService
from contexts.activities.application.ports import (
    ActivityActivationRepository,
    ActivitySessionCloser,
    ActivityTypeOptInReader,
    ActivityTypeReader,
)
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.domain.errors import (
    ActivityActivationNotFound,
    ActivityAlreadyActive,
)
from contexts.activities.domain.models import ActivityActivation, ActivityActivationEndResult
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from shared_kernel import audit

# What tells a delegated activation event apart from a facilitator's in the audit
# trail ([R30.37]). `via` is spelled out rather than left implicit in the presence
# of an agent id, so one stable key filters both actions.
_VIA_AGENT_TOOL = "agent_tool"


def _delegation_metadata(key: str, agent_id: uuid.UUID | None) -> dict[str, str]:
    """The two audit keys a delegated activation carries, or nothing at all.

    ``key`` differs per action on purpose. An agent holding the grant may end a
    round a *teacher* started, so recording that agent under `started_by_agent_id`
    on the ended event would assert something false about who started it; the ended
    event says `ended_by_agent_id` instead. Emitting nothing at all on the
    facilitator path keeps the historical rows honest — they were written by code
    that made no claim either way.
    """
    if agent_id is None:
        return {}
    return {key: str(agent_id), "via": _VIA_AGENT_TOOL}


class ActivationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        activation_repo: ActivityActivationRepository,
        type_repo: ActivityTypeReader,
        optin_repo: ActivityTypeOptInReader | None = None,
        session_repo: ActivitySessionCloser | None = None,
    ) -> None:
        self._db = db
        self._repo = activation_repo
        self._type_repo = type_repo
        # Defaulted from the session so the facade's construction stays a one-liner
        # and a test double supplying only the two repos it exercises keeps working;
        # the opt-in table is read only for platform-scoped types.
        self._optin_repo = optin_repo or ProjectActivityTypeOptInRepository(db)
        # Ending a round closes the sessions answered under it, so this service
        # owns a session writer -- narrowed to that one method (see the port), and
        # defaulted for the same reason as the opt-in reader above.
        self._session_repo: ActivitySessionCloser = session_repo or ActivitySessionRepository(db)
        self._policy = ActivityPolicyService(db)

    async def start(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        started_by_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
        started_by_agent_id: uuid.UUID | None = None,
    ) -> ActivityActivation:
        """Start a round. ``started_by_agent_id`` marks a delegated start ([R30.37]).

        The delegated path differs only in what is *recorded*: both gates below run
        identically for it, in the same order, on this same method — there is no
        second code path an agent could take. ``started_by_user_id`` is the granting
        teacher either way.
        """
        # Tenant isolation ([R30.09], [R30.33]): the caller supplies the type id,
        # so this is the gate — not the picker the facilitator chose it from.
        activity_type = await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activity_type_id,
            project_id=project_id,
        )

        # [R30.30] second gate, and the one that gives a tightened policy any
        # reach over the installed base: the type is checked as *stored*, so a
        # policy change applies without the platform rewriting anyone's row.
        # Ordered before create_active so a violating type never produces an
        # activation, an audit event, or a room broadcast.
        await self._policy.assert_type_allowed(activity_type)

        activation_id = await self._repo.create_active(
            chatroom_id=chatroom_id,
            activity_type_id=activity_type_id,
            started_by_user_id=started_by_user_id,
            started_by_agent_id=started_by_agent_id,
        )
        if activation_id is None:
            active = await self._repo.get_active(chatroom_id)
            if active is None:  # pragma: no cover - a partial-unique conflict has a winner
                raise ActivityActivationNotFound(str(chatroom_id))
            if active.activity_type_id != activity_type_id:
                raise ActivityAlreadyActive(str(active.id))
            return active

        activation = await self._repo.get(activation_id)
        if activation is None:  # pragma: no cover - inserted in this transaction
            raise ActivityActivationNotFound(str(activation_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity.activation_started",
                actor_user_id=started_by_user_id,
                actor_ip=actor_ip,
                resource_type="activity_activation",
                resource_id=activation.id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "activity_type_id": str(activity_type_id),
                    # Absent, not null, on a human-started round: the two keys are
                    # what makes a delegated round distinguishable in the trail
                    # (AC-12), and a `via: "facilitator"` on every historical row
                    # would be a claim this code never made about them.
                    **_delegation_metadata("started_by_agent_id", started_by_agent_id),
                },
                request_id=request_id,
            ),
        )
        return activation

    async def end(
        self,
        *,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
        ended_by_agent_id: uuid.UUID | None = None,
    ) -> ActivityActivationEndResult:
        """End a round. ``ended_by_agent_id`` marks a delegated end ([R30.37]).

        Recorded on the audit event only — the ended activation keeps
        ``started_by_agent_id`` as the record of who *started* it, which is a
        different fact and must not be overwritten by whoever ended it.
        """
        activation = await self._repo.get(activation_id)
        if activation is None or activation.chatroom_id != chatroom_id:
            raise ActivityActivationNotFound(str(activation_id))
        if await self._repo.end(activation_id):
            # The round is over, so nothing answered under it stays open ([R30.22]).
            # Every path that ends an activation -- the facilitator's route, a type
            # delete, an admin platform-type delete, a project opt-out -- goes
            # through here, which is what makes this one call the whole cascade.
            # Ordered before the audit so the count is a fact about what happened,
            # not a prediction.
            sessions_closed = await self._session_repo.close_open_for_activation(activation_id)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.activation_ended",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="activity_activation",
                    resource_id=activation_id,
                    metadata={
                        "chatroom_id": str(chatroom_id),
                        "activity_type_id": str(activation.activity_type_id),
                        "sessions_closed": str(sessions_closed),
                        **_delegation_metadata("ended_by_agent_id", ended_by_agent_id),
                    },
                    request_id=request_id,
                ),
            )
            updated = await self._repo.get(activation_id)
            if updated is not None:
                return ActivityActivationEndResult(activation=updated, transitioned=True)
        return ActivityActivationEndResult(activation=activation, transitioned=False)

    async def get_active(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        return await self._repo.get_active(chatroom_id)


__all__ = ["ActivationService"]
