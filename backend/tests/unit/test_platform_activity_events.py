"""Deadline and all-submitted facts, and the time limit a round snapshots ([R15.28], [R30.46]).

Spec: ``docs/tasks/2026-10-27-crat-facilitation-phase4-autonomous-teacher/spec.md`` §6
(phase 4 AC-3, AC-4, AC-5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import contexts.activities.application.platform_events as pe
from contexts.activities.application.activation_service import ActivationService
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ConnectedProgress,
    ValidatorKind,
)
from contexts.conversation.application.room_presence import RoomPresenceSnapshot

_T0 = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC)


def _type(**over: object) -> ActivityType:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "char",
        "name": '拆字\n"蓋樓"',
        "payload_schema": {"type": "object"},
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 0},
        "retention_days": None,
        "version": 1,
        "created_at": _T0,
    }
    base.update(over)
    return ActivityType(**base)  # type: ignore[arg-type]


def _activation(room: uuid.UUID, type_id: uuid.UUID, **over: object) -> ActivityActivation:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "chatroom_id": room,
        "activity_type_id": type_id,
        "started_by_user_id": uuid.uuid4(),
        "status": ActivationStatus.ACTIVE,
        "created_at": _T0,
    }
    base.update(over)
    return ActivityActivation(**base)  # type: ignore[arg-type]


class TestNotices:
    def test_the_notices_carry_counts_and_a_one_line_name_only(self) -> None:
        activity_type = _type()

        assert pe.deadline_notice(activity_type) == 'The time limit of "拆字 蓋樓" was reached.'
        assert pe.completion_notice(activity_type, 4) == (
            'Every connected participant (4) has a valid submission to "拆字 蓋樓".'
        )
        assert pe.deadline_notice(None) == "The time limit of the activity was reached."

    def test_progress_is_complete_only_with_someone_connected(self) -> None:
        assert ConnectedProgress(connected=3, submitted=3).complete
        assert not ConnectedProgress(connected=3, submitted=2).complete
        assert not ConnectedProgress(connected=0, submitted=0).complete


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, group: bool = False) -> None:
        self.room = uuid.uuid4()
        self.creator = uuid.uuid4()
        self.type = _type(group_config={"consent": {"numerator": 2, "denominator": 3}} if group else None)
        self.activation = _activation(self.room, self.type.id)
        self.connected: list[uuid.UUID] = []
        self.submitted: set[uuid.UUID] = set()
        self.available = True
        self.truncated = False
        self.claimed = False
        self.audit = AsyncMock()
        monkeypatch.setattr(pe.audit, "emit", self.audit)

        async def _claim(activation_id):
            if self.claimed:
                return False
            self.claimed = True
            return True

        async def _presence(room_id, *, limit):
            return RoomPresenceSnapshot(
                observed_at=_T0,
                available=self.available,
                user_ids=tuple(self.connected),
                truncated=self.truncated,
            )

        conversation = SimpleNamespace(
            get_chatroom=AsyncMock(return_value=SimpleNamespace(created_by_user_id=self.creator)),
            room_presence_snapshot=_presence,
        )
        monkeypatch.setattr(
            "contexts.conversation.interfaces.facade.ConversationFacade", lambda _db: conversation
        )
        self.svc = pe.PlatformActivityEvents.__new__(pe.PlatformActivityEvents)
        self.svc._db = MagicMock()
        self.svc._activations = SimpleNamespace(
            get=AsyncMock(side_effect=lambda _id: self.activation), claim_completion=_claim
        )
        self.svc._types = SimpleNamespace(get=AsyncMock(return_value=self.type))
        self.svc._submissions = SimpleNamespace(
            valid_individual_subjects=AsyncMock(side_effect=lambda **_: set(self.submitted))
        )

    async def check(self):
        return await self.svc.claim_if_complete(chatroom_id=self.room, activation_id=self.activation.id)


class TestAllSubmitted:
    async def test_fires_once_when_every_connected_student_has_submitted(self, monkeypatch) -> None:
        """AC-5."""
        h = _Harness(monkeypatch)
        a, b = uuid.uuid4(), uuid.uuid4()
        h.connected = [h.creator, a, b]
        h.submitted = {a}
        assert await h.check() is None

        h.submitted = {a, b}
        event = await h.check()

        assert event is not None
        assert event.notice == 'Every connected participant (2) has a valid submission to "拆字 蓋樓".'
        assert event.activity_type_id == h.type.id
        assert await h.check() is None
        assert h.audit.await_count == 1
        assert h.audit.await_args.args[1].action == "activity.all_submitted"

    async def test_a_disconnected_student_does_not_hold_the_class(self, monkeypatch) -> None:
        """AC-5: the count covers connected participants only."""
        h = _Harness(monkeypatch)
        a = uuid.uuid4()
        h.connected = [a]
        h.submitted = {a}

        assert await h.check() is not None

    async def test_nobody_but_the_creator_connected_claims_nothing(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.connected = [h.creator]

        assert await h.check() is None

    @pytest.mark.parametrize("failure", ["unavailable", "truncated"])
    async def test_an_unreadable_connection_set_claims_nothing(self, monkeypatch, failure: str) -> None:
        h = _Harness(monkeypatch)
        a = uuid.uuid4()
        h.connected, h.submitted = [a], {a}
        h.available = failure != "unavailable"
        h.truncated = failure == "truncated"

        assert await h.check() is None
        assert not h.claimed

    async def test_a_group_activity_never_fires(self, monkeypatch) -> None:
        """FU-2."""
        h = _Harness(monkeypatch, group=True)
        a = uuid.uuid4()
        h.connected, h.submitted = [a], {a}

        assert await h.check() is None

    async def test_an_ended_or_foreign_activation_never_fires(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        a = uuid.uuid4()
        h.connected, h.submitted = [a], {a}
        h.activation = _activation(uuid.uuid4(), h.type.id)
        assert await h.check() is None

        h.activation = _activation(h.room, h.type.id, status=ActivationStatus.ENDED)
        assert await h.check() is None


class TestDeadlines:
    async def test_each_claimed_deadline_is_audited_and_returned(self, monkeypatch) -> None:
        """AC-4."""
        emit = AsyncMock()
        monkeypatch.setattr(pe.audit, "emit", emit)
        activity_type = _type()
        activation = _activation(uuid.uuid4(), activity_type.id, time_limit_minutes=5)
        svc = pe.PlatformActivityEvents.__new__(pe.PlatformActivityEvents)
        svc._db = MagicMock()
        svc._activations = SimpleNamespace(claim_due_deadlines=AsyncMock(return_value=[activation]))
        svc._types = SimpleNamespace(get=AsyncMock(return_value=activity_type))

        events = await svc.claim_due_deadlines(limit=10)

        assert [(e.chatroom_id, e.activation_id) for e in events] == [(activation.chatroom_id, activation.id)]
        assert events[0].notice == 'The time limit of "拆字 蓋樓" was reached.'
        assert emit.await_args.args[1].action == "activity.deadline_reached"
        assert emit.await_args.args[1].metadata["time_limit_minutes"] == "5"


class TestTimeLimitResolution:
    """AC-3: the room override, else the type default, else none."""

    def _svc(self, overrides: dict) -> ActivationService:
        return ActivationService(
            MagicMock(),
            activation_repo=MagicMock(),
            type_repo=MagicMock(),
            time_limits=MagicMock(for_room=AsyncMock(return_value=overrides)),
        )

    async def test_the_type_default_applies_without_an_override(self) -> None:
        activity_type = _type(default_time_limit_minutes=5)
        assert await self._svc({}).resolve_time_limit(uuid.uuid4(), activity_type) == 5

    async def test_a_room_override_wins(self) -> None:
        activity_type = _type(default_time_limit_minutes=5)
        assert await self._svc({activity_type.id: 8}).resolve_time_limit(uuid.uuid4(), activity_type) == 8

    async def test_a_stored_no_limit_override_beats_the_default(self) -> None:
        activity_type = _type(default_time_limit_minutes=5)
        assert (
            await self._svc({activity_type.id: None}).resolve_time_limit(uuid.uuid4(), activity_type) is None
        )

    async def test_neither_means_no_limit(self) -> None:
        assert await self._svc({uuid.uuid4(): 9}).resolve_time_limit(uuid.uuid4(), _type()) is None
