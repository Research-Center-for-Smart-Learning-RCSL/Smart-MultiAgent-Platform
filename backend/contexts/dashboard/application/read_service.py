"""Compose current-round activity, room, identity, and tenancy reads."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from contexts.dashboard.domain.models import (
    DashboardActivation,
    DashboardMessageCounts,
    DashboardRoom,
    DashboardRound,
    DashboardRoundAggregate,
    DashboardRoundSubject,
    DashboardSubjectKind,
    DashboardSummary,
    DashboardThresholds,
    DashboardTimeseriesBucket,
    DashboardWatchlist,
    DashboardWatchlistCandidate,
    DashboardWatchlistEntry,
    RoundState,
    TrafficLight,
)

_GREEN_MINUTES = 5
_YELLOW_MINUTES = 15


def _traffic_light(
    *,
    is_active: bool,
    started_at: dt.datetime,
    last_valid_submission_at: dt.datetime | None,
    now_at: dt.datetime,
) -> TrafficLight:
    if not is_active:
        return TrafficLight.INACTIVE
    latest = max(started_at, last_valid_submission_at or started_at)
    elapsed = (now_at - latest).total_seconds() / 60
    if elapsed <= _GREEN_MINUTES:
        return TrafficLight.GREEN
    if elapsed <= _YELLOW_MINUTES:
        return TrafficLight.YELLOW
    return TrafficLight.RED


class ActivitiesReader(Protocol):
    async def current_activations_for_rooms(
        self, *, chatroom_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, DashboardActivation]: ...

    async def round_aggregates(
        self, *, activation_ids: Sequence[uuid.UUID]
    ) -> list[DashboardRoundAggregate]: ...

    async def round_session_subjects(
        self, *, activation_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, set[DashboardRoundSubject]]: ...

    async def timeseries_for_rooms(
        self, *, chatroom_ids: Sequence[uuid.UUID], since: dt.datetime, bucket_seconds: int
    ) -> list[DashboardTimeseriesBucket]: ...

    async def watchlist_for_rounds(
        self,
        *,
        rounds: Sequence[tuple[uuid.UUID, uuid.UUID, dt.datetime, bool]],
        roster: dict[uuid.UUID, set[tuple[uuid.UUID, str]]],
        session_subjects: dict[uuid.UUID, set[DashboardRoundSubject]],
    ) -> list[DashboardWatchlistCandidate]: ...

    async def resolve_member_group_names(self, group_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]: ...


class ConversationReader(Protocol):
    async def room_online_counts(self, chatroom_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int | None]: ...

    async def message_counts_by_sender(
        self, rounds: Sequence[tuple[uuid.UUID, dt.datetime]]
    ) -> dict[uuid.UUID, dict[str, int]]: ...

    async def round_roster(
        self, rounds: Sequence[tuple[uuid.UUID, dt.datetime]]
    ) -> dict[uuid.UUID, set[tuple[uuid.UUID, str]]]: ...

    async def guest_labels(self, session_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]: ...


class IdentityReader(Protocol):
    async def get_display_names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str | None]: ...


class TenancyReader(Protocol):
    async def project_owner_ids(
        self, user_ids: Sequence[uuid.UUID], *, project_id: uuid.UUID
    ) -> set[uuid.UUID]: ...


class DashboardReadService:
    def __init__(
        self,
        *,
        activities: ActivitiesReader,
        conversation: ConversationReader,
        identity: IdentityReader,
        tenancy: TenancyReader,
        idle_minutes: int,
    ) -> None:
        self._activities = activities
        self._conversation = conversation
        self._identity = identity
        self._tenancy = tenancy
        self._idle_minutes = idle_minutes

    def thresholds(self) -> DashboardThresholds:
        return DashboardThresholds(
            green_minutes=_GREEN_MINUTES,
            yellow_minutes=_YELLOW_MINUTES,
            idle_minutes=self._idle_minutes,
        )

    async def summary(self, room_map: Mapping[uuid.UUID, str]) -> DashboardSummary:
        generated_at = dt.datetime.now(dt.UTC)
        if not room_map:
            return DashboardSummary(rooms=[], thresholds=self.thresholds(), generated_at=generated_at)

        activations = await self._activities.current_activations_for_rooms(chatroom_ids=list(room_map))
        activation_ids = [activation.activation_id for activation in activations.values()]
        aggregates = await self._activities.round_aggregates(activation_ids=activation_ids)
        aggregate_by_activation = {aggregate.activation_id: aggregate for aggregate in aggregates}
        round_scopes = [(room_id, activation.started_at) for room_id, activation in activations.items()]
        online_counts = await self._conversation.room_online_counts(list(room_map))
        message_counts = await self._conversation.message_counts_by_sender(round_scopes)
        roster = await self._conversation.round_roster(round_scopes)
        session_subjects = await self._activities.round_session_subjects(activation_ids=activation_ids)

        participant_counts: dict[uuid.UUID, int] = {}
        for room_id, activation in activations.items():
            participants = set(roster.get(room_id, set()))
            participants.update(
                (subject.subject_id, subject.kind.value)
                for subject in session_subjects.get(activation.activation_id, set())
            )
            participant_counts[room_id] = len(participants)

        rooms: list[DashboardRoom] = []
        for room_id, room_name in room_map.items():
            current_activation = activations.get(room_id)
            aggregate = (
                aggregate_by_activation.get(current_activation.activation_id) if current_activation else None
            )
            is_active = current_activation is not None and current_activation.is_active
            last_valid_at = aggregate.last_valid_submission_at if aggregate else None
            round_data = (
                DashboardRound(
                    activation_id=current_activation.activation_id,
                    state=RoundState.ACTIVE if is_active else RoundState.ENDED,
                    started_at=current_activation.started_at,
                    ended_at=current_activation.ended_at,
                )
                if current_activation is not None
                else None
            )
            rooms.append(
                DashboardRoom(
                    room_id=room_id,
                    room_name=room_name,
                    round=round_data,
                    total_submissions=aggregate.total_submissions if aggregate else 0,
                    valid_count=aggregate.valid_count if aggregate else 0,
                    last_valid_submission_at=last_valid_at,
                    status=_traffic_light(
                        is_active=is_active,
                        started_at=current_activation.started_at if current_activation else generated_at,
                        last_valid_submission_at=last_valid_at,
                        now_at=generated_at,
                    ),
                    online_count=online_counts.get(room_id),
                    participant_count=participant_counts.get(room_id, 0),
                    message_counts=DashboardMessageCounts(
                        **message_counts.get(room_id, {"human": 0, "agent": 0})
                    ),
                )
            )
        return DashboardSummary(rooms=rooms, thresholds=self.thresholds(), generated_at=generated_at)

    async def timeseries(
        self,
        room_map: Mapping[uuid.UUID, str],
        *,
        since: dt.datetime,
        bucket_seconds: int,
    ) -> list[DashboardTimeseriesBucket]:
        if not room_map:
            return []
        return await self._activities.timeseries_for_rooms(
            chatroom_ids=list(room_map), since=since, bucket_seconds=bucket_seconds
        )

    async def watchlist(
        self,
        room_map: Mapping[uuid.UUID, str],
        *,
        project_id: uuid.UUID,
    ) -> DashboardWatchlist:
        if not room_map:
            return DashboardWatchlist(entries=[])
        activities = self._activities
        activations = await activities.current_activations_for_rooms(chatroom_ids=list(room_map))
        current_rounds = [(room_id, activation) for room_id, activation in activations.items()]
        if not current_rounds:
            return DashboardWatchlist(entries=[])

        roster = await self._conversation.round_roster(
            [(room_id, activation.started_at) for room_id, activation in current_rounds]
        )
        activation_ids = [activation.activation_id for _, activation in current_rounds]
        session_subjects = await activities.round_session_subjects(activation_ids=activation_ids)
        candidate_users = {
            subject_id
            for members in roster.values()
            for subject_id, kind in members
            if kind == DashboardSubjectKind.USER.value
        }
        candidate_users.update(
            subject.subject_id
            for subjects in session_subjects.values()
            for subject in subjects
            if subject.kind is DashboardSubjectKind.USER
        )
        project_owner_ids = await self._tenancy.project_owner_ids(
            list(candidate_users), project_id=project_id
        )
        filtered_roster = {
            room_id: {
                (subject_id, kind)
                for subject_id, kind in members
                if kind != DashboardSubjectKind.USER.value or subject_id not in project_owner_ids
            }
            for room_id, members in roster.items()
        }
        filtered_session_subjects = {
            activation_id: {
                subject
                for subject in subjects
                if (
                    subject.kind is not DashboardSubjectKind.USER
                    or subject.subject_id not in project_owner_ids
                )
            }
            for activation_id, subjects in session_subjects.items()
        }
        rounds = [
            (room_id, activation.activation_id, activation.started_at, activation.is_active)
            for room_id, activation in current_rounds
        ]
        entries = await activities.watchlist_for_rounds(
            rounds=rounds,
            roster=filtered_roster,
            session_subjects=filtered_session_subjects,
        )
        user_ids = [entry.subject_id for entry in entries if entry.kind is DashboardSubjectKind.USER]
        guest_ids = [entry.subject_id for entry in entries if entry.kind is DashboardSubjectKind.GUEST]
        group_ids = [entry.subject_id for entry in entries if entry.kind is DashboardSubjectKind.MEMBER_GROUP]
        user_names = await self._identity.get_display_names(user_ids)
        guest_names = await self._conversation.guest_labels(guest_ids)
        group_names = await activities.resolve_member_group_names(group_ids)

        output: list[DashboardWatchlistEntry] = []
        for entry in entries:
            if entry.kind is DashboardSubjectKind.USER:
                kind: Literal["user", "guest", "group"] = "user"
                code = _user_subject_code(entry.subject_id)
                display_name = user_names.get(entry.subject_id)
            elif entry.kind is DashboardSubjectKind.GUEST:
                kind = "guest"
                code = f"guest:{str(entry.subject_id)[:8]}"
                display_name = guest_names.get(entry.subject_id)
            else:
                kind = "group"
                code = _group_subject_code(entry.subject_id)
                display_name = group_names.get(entry.subject_id)
            reason = _watchlist_reason(entry.reason)
            output.append(
                DashboardWatchlistEntry(
                    room_id=entry.room_id,
                    kind=kind,
                    display_name=display_name,
                    subject_code=code,
                    valid_count=entry.valid_count,
                    last_valid_submission_at=entry.last_valid_submission_at,
                    needs_attention=entry.needs_attention,
                    reason=reason,
                )
            )
        return DashboardWatchlist(entries=output)


def _watchlist_reason(value: str | None) -> Literal["no_valid_in_round", "idle"] | None:
    if value == "no_valid_in_round":
        return "no_valid_in_round"
    if value == "idle":
        return "idle"
    return None


def _user_subject_code(subject_id: uuid.UUID) -> str:
    return f"u:{str(subject_id)[:8]}"


def _group_subject_code(subject_id: uuid.UUID) -> str:
    return f"g:{str(subject_id)[:8]}"
