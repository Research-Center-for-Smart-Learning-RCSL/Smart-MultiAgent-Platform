"""Creativity means on the dashboard watchlist (research data integrity spec, section 5, Q-11).

Owner or admin only: for any other member the scores are not read at all, and
the field is null. Matched to a watchlist entry by room and subject.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.dashboard import dashboard_watchlist
from contexts.dashboard.application.read_service import DashboardReadService
from contexts.dashboard.domain.models import (
    DashboardActivation,
    DashboardCreativity,
    DashboardCreativityOut,
    DashboardSubjectKind,
    DashboardWatchlist,
    DashboardWatchlistCandidate,
    DashboardWatchlistEntry,
)
from contexts.dashboard.interfaces.facade import DashboardFacade

_NOW = dt.datetime(2026, 11, 8, 10, 0, tzinfo=dt.UTC)


def _service(room_id: uuid.UUID, students: list[uuid.UUID], creativity: list[DashboardCreativity]):
    activities = AsyncMock()
    activities.current_activations_for_rooms.return_value = {
        room_id: DashboardActivation(
            activation_id=uuid.uuid4(), is_active=True, started_at=_NOW, ended_at=None
        )
    }
    activities.round_session_subjects.return_value = {}
    activities.watchlist_for_rounds.return_value = [
        DashboardWatchlistCandidate(
            room_id=room_id,
            subject_id=s,
            kind=DashboardSubjectKind.USER,
            valid_count=1,
            last_valid_submission_at=_NOW,
            needs_attention=False,
            reason=None,
        )
        for s in students
    ]
    activities.resolve_member_group_names.return_value = {}
    activities.creativity_for_rooms.return_value = creativity
    conversation = AsyncMock()
    conversation.room_presence.return_value = None
    conversation.round_roster.return_value = {}
    conversation.guest_labels.return_value = {}
    identity = AsyncMock()
    identity.get_display_names.return_value = {}
    tenancy = AsyncMock()
    tenancy.project_owner_ids.return_value = set()
    service = DashboardReadService(
        activities=activities, conversation=conversation, identity=identity, tenancy=tenancy, idle_minutes=10
    )
    return service, activities


def _row(room_id: uuid.UUID, subject_id: uuid.UUID, **over: object) -> DashboardCreativity:
    base: dict[str, object] = {
        "room_id": room_id,
        "subject_id": subject_id,
        "scored_rounds": 2,
        "fluency": 3.5,
        "flexibility": 2.0,
        "originality": 1.5,
        "convergence": 4.0,
        "last_status": "scored",
        "last_reason": None,
    }
    base.update(over)
    return DashboardCreativity(**base)  # type: ignore[arg-type]


class TestTheReadService:
    async def test_without_permission_no_score_is_read(self) -> None:
        room, student = uuid.uuid4(), uuid.uuid4()
        service, activities = _service(room, [student], [_row(room, student)])

        result = await service.watchlist({room: "Room"}, project_id=uuid.uuid4())

        activities.creativity_for_rooms.assert_not_called()
        assert [e.creativity for e in result.entries] == [None]

    async def test_with_permission_each_entry_gets_its_own_room_and_subject(self) -> None:
        room, other_room = uuid.uuid4(), uuid.uuid4()
        scored, errored, unscored = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        service, _activities = _service(
            room,
            [scored, errored, unscored],
            [
                _row(room, scored),
                _row(
                    room,
                    errored,
                    scored_rounds=0,
                    fluency=None,
                    flexibility=None,
                    originality=None,
                    convergence=None,
                    last_status="error",
                    last_reason="judge_bad_output",
                ),
                # The same person in another room is not this room's score.
                _row(other_room, unscored),
            ],
        )

        result = await service.watchlist({room: "Room"}, project_id=uuid.uuid4(), include_creativity=True)

        by_subject = {e.subject_code: e.creativity for e in result.entries}
        assert by_subject[f"u:{str(scored)[:8]}"] == DashboardCreativityOut(
            scored_rounds=2,
            fluency=3.5,
            flexibility=2.0,
            originality=1.5,
            convergence=4.0,
            last_status="scored",
            last_reason=None,
        )
        errored_out = by_subject[f"u:{str(errored)[:8]}"]
        assert errored_out is not None
        assert (errored_out.originality, errored_out.last_reason) == (None, "judge_bad_output")
        assert by_subject[f"u:{str(unscored)[:8]}"] is None


def _entry(creativity: DashboardCreativityOut | None) -> DashboardWatchlistEntry:
    return DashboardWatchlistEntry(
        room_id=uuid.uuid4(),
        kind="user",
        display_name="A",
        subject_code="u:1",
        valid_count=1,
        last_valid_submission_at=_NOW,
        needs_attention=False,
        reason=None,
        creativity=creativity,
    )


class TestTheRoute:
    @pytest.mark.parametrize("owner_or_admin", [True, False])
    async def test_the_route_asks_for_scores_only_for_an_owner_or_admin(self, owner_or_admin: bool) -> None:
        watchlist = AsyncMock(return_value=DashboardWatchlist(entries=[_entry(None)]))
        project_id = uuid.uuid4()
        with (
            patch(
                "app.api.v1.dashboard._resolve_workspace_rooms",
                new=AsyncMock(return_value=(project_id, {uuid.uuid4(): "Room"})),
            ),
            patch(
                "app.api.v1.dashboard.is_project_owner_or_admin", new=AsyncMock(return_value=owner_or_admin)
            ),
            patch.object(DashboardFacade, "watchlist", new=watchlist),
        ):
            await dashboard_watchlist(workspace_id=uuid.uuid4(), principal=SimpleNamespace(), db=AsyncMock())

        assert watchlist.await_args.kwargs["include_creativity"] is owner_or_admin

    async def test_the_response_carries_the_means(self) -> None:
        means = DashboardCreativityOut(
            scored_rounds=1,
            fluency=2.0,
            flexibility=None,
            originality=3.0,
            convergence=1.0,
            last_status="scored",
            last_reason=None,
        )
        with (
            patch(
                "app.api.v1.dashboard._resolve_workspace_rooms",
                new=AsyncMock(return_value=(uuid.uuid4(), {uuid.uuid4(): "Room"})),
            ),
            patch("app.api.v1.dashboard.is_project_owner_or_admin", new=AsyncMock(return_value=True)),
            patch.object(
                DashboardFacade,
                "watchlist",
                new=AsyncMock(return_value=DashboardWatchlist(entries=[_entry(means)])),
            ),
        ):
            out = await dashboard_watchlist(
                workspace_id=uuid.uuid4(), principal=SimpleNamespace(), db=AsyncMock()
            )

        creativity = out.entries[0].creativity
        assert creativity is not None
        assert (creativity.originality, creativity.flexibility, creativity.scored_rounds) == (3.0, None, 1)
