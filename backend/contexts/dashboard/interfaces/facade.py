"""Public read facade for teacher dashboard endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.interfaces.facade import (
    ActivationStatus,
    ActivitiesFacade,
    RoundSubject,
    SubjectKind,
)
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.dashboard.application.read_service import DashboardReadService
from contexts.dashboard.domain.models import (
    DashboardActivation,
    DashboardRoundAggregate,
    DashboardRoundSubject,
    DashboardSubjectKind,
    DashboardSummary,
    DashboardTimeseriesBucket,
    DashboardWatchlist,
    DashboardWatchlistCandidate,
)
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.tenancy.interfaces.facade import TenancyFacade


class _ActivitiesDashboardReader:
    """Translate activity facade models into dashboard-owned read models."""

    def __init__(self, facade: ActivitiesFacade) -> None:
        self._facade = facade

    async def current_activations_for_rooms(
        self, *, chatroom_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, DashboardActivation]:
        activations = await self._facade.current_activations_for_rooms(chatroom_ids=chatroom_ids)
        return {
            room_id: DashboardActivation(
                activation_id=activation.id,
                is_active=activation.status is ActivationStatus.ACTIVE,
                started_at=activation.created_at,
                ended_at=activation.ended_at,
            )
            for room_id, activation in activations.items()
        }

    async def round_aggregates(self, *, activation_ids: Sequence[uuid.UUID]) -> list[DashboardRoundAggregate]:
        aggregates = await self._facade.round_aggregates(activation_ids=activation_ids)
        return [
            DashboardRoundAggregate(
                activation_id=aggregate.activation_id,
                total_submissions=aggregate.total_submissions,
                valid_count=aggregate.valid_count,
                last_valid_submission_at=aggregate.last_valid_submission_at,
            )
            for aggregate in aggregates
        ]

    async def round_session_subjects(
        self, *, activation_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, set[DashboardRoundSubject]]:
        subjects = await self._facade.round_session_subjects(activation_ids=activation_ids)
        return {
            activation_id: {
                DashboardRoundSubject(
                    subject_id=subject.subject_id,
                    kind=DashboardSubjectKind(subject.subject_kind.value),
                )
                for subject in activation_subjects
            }
            for activation_id, activation_subjects in subjects.items()
        }

    async def timeseries_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        since: dt.datetime,
        bucket_seconds: int,
    ) -> list[DashboardTimeseriesBucket]:
        buckets = await self._facade.timeseries_for_rooms(
            chatroom_ids=chatroom_ids,
            since=since,
            bucket_seconds=bucket_seconds,
        )
        return [
            DashboardTimeseriesBucket(room_id=bucket.chatroom_id, bucket=bucket.bucket, count=bucket.count)
            for bucket in buckets
        ]

    async def watchlist_for_rounds(
        self,
        *,
        rounds: Sequence[tuple[uuid.UUID, uuid.UUID, dt.datetime, bool]],
        roster: dict[uuid.UUID, set[tuple[uuid.UUID, str]]],
        session_subjects: dict[uuid.UUID, set[DashboardRoundSubject]],
    ) -> list[DashboardWatchlistCandidate]:
        activity_subjects = {
            activation_id: {
                RoundSubject(
                    subject_kind=SubjectKind(subject.kind.value),
                    subject_id=subject.subject_id,
                )
                for subject in activation_subjects
            }
            for activation_id, activation_subjects in session_subjects.items()
        }
        entries = await self._facade.watchlist_for_rounds(
            rounds=rounds,
            roster=roster,
            session_subjects=activity_subjects,
        )
        return [
            DashboardWatchlistCandidate(
                room_id=entry.chatroom_id,
                subject_id=entry.subject_id,
                kind=DashboardSubjectKind(entry.subject_kind.value),
                valid_count=entry.valid_count,
                last_valid_submission_at=entry.last_valid_submission_at,
                needs_attention=entry.needs_attention,
                reason=entry.reason,
            )
            for entry in entries
        ]

    async def resolve_member_group_names(self, group_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        return await self._facade.resolve_member_group_names(group_ids)


class DashboardFacade:
    GREEN_MINUTES = 5
    YELLOW_MINUTES = 15
    IDLE_MINUTES = ActivitiesFacade.WATCHLIST_IDLE_MINUTES

    def __init__(self, db: AsyncSession) -> None:
        activities = ActivitiesFacade(db)
        self._service = DashboardReadService(
            activities=_ActivitiesDashboardReader(activities),
            conversation=ConversationFacade(db),
            identity=IdentityFacade(db),
            tenancy=TenancyFacade(db),
            idle_minutes=activities.WATCHLIST_IDLE_MINUTES,
        )

    async def summary(self, room_map: Mapping[uuid.UUID, str]) -> DashboardSummary:
        return await self._service.summary(room_map)

    async def timeseries(
        self,
        room_map: Mapping[uuid.UUID, str],
        *,
        since: dt.datetime,
        bucket_seconds: int,
    ) -> list[DashboardTimeseriesBucket]:
        return await self._service.timeseries(room_map, since=since, bucket_seconds=bucket_seconds)

    async def watchlist(
        self, room_map: Mapping[uuid.UUID, str], *, project_id: uuid.UUID
    ) -> DashboardWatchlist:
        return await self._service.watchlist(room_map, project_id=project_id)
