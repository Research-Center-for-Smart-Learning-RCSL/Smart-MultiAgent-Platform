"""The two delegated activity-control tools ([R30.37]).

Covers AC-5 through AC-10 and AC-13: which turns carry the tools at all, what the
``start_activity`` enum may contain, that both server-side gates still apply on the
delegated path, that ``end_activity`` is bounded by the same allowlist as
``start_activity``, and that nothing reaches a room before the turn commits.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.activities.domain.errors import ActivityTypeNotFound, ActivityTypeViolatesPolicy
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityType,
    ValidatorKind,
)
from contexts.agents.application.runtime import activity_tools as act
from contexts.agents.application.runtime import builtin_tools as bt
from contexts.conversation.domain.models import ActivityControlGrant

_NOW = dt.datetime(2026, 8, 18, tzinfo=dt.UTC)
_ROOM = uuid.uuid4()
_PROJECT = uuid.uuid4()
_GRANTER = uuid.uuid4()


def _session() -> AsyncMock:
    """A stand-in turn session that supports the savepointed audit write."""
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.info = {}
    return db


def _agent(name: str = "TA") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=_PROJECT, name=name)


def _type(key: str = "quiz", name: str = "Quiz") -> ActivityType:
    return ActivityType(
        id=uuid.uuid4(),
        project_id=_PROJECT,
        key=key,
        name=name,
        payload_schema={},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "quiz"},
        retention_days=None,
        version=1,
        created_at=_NOW,
    )


def _grant(*type_ids: uuid.UUID, agent_id: uuid.UUID | None = None) -> ActivityControlGrant:
    return ActivityControlGrant(
        agent_id=agent_id or uuid.uuid4(),
        granted_by_user_id=_GRANTER,
        activity_type_ids=tuple(type_ids),
    )


def _control(*types: ActivityType, grant: ActivityControlGrant | None = None) -> act.ActivityControlContext:
    return act.ActivityControlContext(
        chatroom_id=_ROOM,
        project_id=_PROJECT,
        grant=grant or _grant(*[t.id for t in types]),
        allowed_types=types,
    )


class _FakeActivitiesFacade:
    """One stand-in for both the resolution and the tool call paths.

    Class-level state, reset per test: the production code constructs a fresh
    facade inside each ``invoke``, so per-instance state would not survive between
    the assembly read and the tool call.
    """

    def __init__(self, db: object) -> None:
        pass

    reachable: ClassVar[dict[uuid.UUID, ActivityType]] = {}
    started: ClassVar[list[dict[str, Any]]] = []
    ended: ClassVar[list[dict[str, Any]]] = []
    start_error: ClassVar[Exception | None] = None
    active: ClassVar[ActivityActivation | None] = None
    end_transitioned: ClassVar[bool] = True

    @classmethod
    def reset(cls) -> None:
        cls.reachable = {}
        cls.started = []
        cls.ended = []
        cls.start_error = None
        cls.active = None
        cls.end_transitioned = True

    async def resolve_type_for_project(
        self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID
    ) -> ActivityType:
        found = _FakeActivitiesFacade.reachable.get(activity_type_id)
        if found is None:
            raise ActivityTypeNotFound(str(activity_type_id))
        return found

    async def start_activation(self, **kw: Any) -> ActivityActivation:
        if _FakeActivitiesFacade.start_error is not None:
            raise _FakeActivitiesFacade.start_error
        _FakeActivitiesFacade.started.append(kw)
        return ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=kw["chatroom_id"],
            activity_type_id=kw["activity_type_id"],
            started_by_user_id=kw["started_by_user_id"],
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=kw.get("started_by_agent_id"),
        )

    async def get_active_activation(self, chatroom_id: uuid.UUID) -> ActivityActivation | None:
        return _FakeActivitiesFacade.active

    async def end_activation(self, **kw: Any) -> ActivityActivationEndResult:
        _FakeActivitiesFacade.ended.append(kw)
        assert _FakeActivitiesFacade.active is not None
        return ActivityActivationEndResult(
            activation=_FakeActivitiesFacade.active,
            transitioned=_FakeActivitiesFacade.end_transitioned,
        )


@pytest.fixture(autouse=True)
def _facade(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeActivitiesFacade.reset()
    monkeypatch.setattr("contexts.activities.interfaces.facade.ActivitiesFacade", _FakeActivitiesFacade)


def _wire_conversation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    grant: ActivityControlGrant | None,
    project_id: uuid.UUID | None = _PROJECT,
    grant_error: Exception | None = None,
) -> None:
    class _Facade:
        def __init__(self, db: object) -> None:
            pass

        async def activity_control_grant(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID):
            if grant_error is not None:
                raise grant_error
            return grant

        async def project_id_for_chatroom(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
            return project_id

    monkeypatch.setattr(act, "ConversationFacade", _Facade)


class TestGrantResolution:
    """What a turn is allowed to build, before any tool exists."""

    async def test_no_grant_yields_no_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-5's negative half, and AC-13's: a revoked grant reads as `None` in
        the repository whatever allowlist it left behind, so nothing is built."""
        _wire_conversation(monkeypatch, grant=None)

        assert (
            await act.resolve_activity_control(_session(), chatroom_id=_ROOM, agent_id=uuid.uuid4())
        ) is None

    async def test_an_error_reading_the_grant_yields_no_control(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed. An exception must never be read as authorization."""
        _wire_conversation(monkeypatch, grant=_grant(), grant_error=RuntimeError("db gone"))

        assert (
            await act.resolve_activity_control(_session(), chatroom_id=_ROOM, agent_id=uuid.uuid4())
        ) is None

    async def test_an_unresolvable_room_yields_no_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _wire_conversation(monkeypatch, grant=_grant(uuid.uuid4()), project_id=None)

        assert (
            await act.resolve_activity_control(_session(), chatroom_id=_ROOM, agent_id=uuid.uuid4())
        ) is None

    async def test_a_stale_id_is_dropped_and_the_rest_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-6's tail: one deleted worksheet must not cost the agent the others."""
        live, stale = _type("unit2"), uuid.uuid4()
        _FakeActivitiesFacade.reachable = {live.id: live}
        _wire_conversation(monkeypatch, grant=_grant(stale, live.id))

        control = await act.resolve_activity_control(_session(), chatroom_id=_ROOM, agent_id=uuid.uuid4())

        assert control is not None
        assert [t.id for t in control.allowed_types] == [live.id]

    async def test_an_entirely_stale_allowlist_yields_no_control(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two tools over an empty enum are worse than no tools: the model would be
        told it may act and then find no legal argument."""
        _wire_conversation(monkeypatch, grant=_grant(uuid.uuid4()))

        assert (
            await act.resolve_activity_control(_session(), chatroom_id=_ROOM, agent_id=uuid.uuid4())
        ) is None


class TestToolAssembly:
    """AC-5, AC-6 and AC-14 at the assembly seam."""

    def test_a_granted_turn_carries_both_tools(self) -> None:
        tools = bt.build_agent_tools(
            _session(),
            agent=_agent(),
            tools=[],
            deps=MagicMock(),
            chatroom_id=_ROOM,
            activity_control=_control(_type()),
        )

        assert {t.name for t in tools} == {"start_activity", "end_activity"}

    def test_an_ungranted_turn_carries_neither(self) -> None:
        tools = bt.build_agent_tools(
            _session(), agent=_agent(), tools=[], deps=MagicMock(), chatroom_id=_ROOM
        )

        assert tools == []

    def test_the_start_enum_is_exactly_the_resolved_keys(self) -> None:
        """AC-6. No client-supplied identifier crosses this boundary at all — the
        model cannot name a type outside the allowlist even in a malformed call."""
        first, second = _type("unit2", "Unit 2"), _type("unit4", "Unit 4")
        tools = {
            t.name: t
            for t in bt.build_agent_tools(
                _session(),
                agent=_agent(),
                tools=[],
                deps=MagicMock(),
                chatroom_id=_ROOM,
                activity_control=_control(first, second),
            )
        }

        schema = tools["start_activity"].input_schema
        assert schema["properties"]["activity_type_key"]["enum"] == ["unit2", "unit4"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["activity_type_key"]
        # The description names each option, so the model can choose by meaning.
        assert "unit2 = Unit 2" in tools["start_activity"].description
        assert "unit4 = Unit 4" in tools["start_activity"].description

    def test_two_types_sharing_a_key_get_distinct_enum_values(self) -> None:
        """[R30.02] lets a project-owned type and an opted-in platform type share a
        key. An ambiguous enum value would resolve to two different worksheets, so
        the second gets a suffix rather than being dropped."""
        owned, platform = _type("quiz", "Owned quiz"), _type("quiz", "Platform quiz")
        tools = {
            t.name: t
            for t in bt.build_agent_tools(
                _session(),
                agent=_agent(),
                tools=[],
                deps=MagicMock(),
                chatroom_id=_ROOM,
                activity_control=_control(owned, platform),
            )
        }

        values = tools["start_activity"].input_schema["properties"]["activity_type_key"]["enum"]
        assert values == ["quiz", "quiz#2"]
        assert len(set(values)) == 2

    def test_end_activity_declares_no_arguments(self) -> None:
        tools = {
            t.name: t
            for t in bt.build_agent_tools(
                _session(),
                agent=_agent(),
                tools=[],
                deps=MagicMock(),
                chatroom_id=_ROOM,
                activity_control=_control(_type()),
            )
        }

        assert tools["end_activity"].input_schema["properties"] == {}

    def test_both_names_are_reserved(self) -> None:
        """AC-14: the drift guard's canonical set must already carry them, or a
        user function could shadow a tool that starts a class-visible round."""
        from contexts.agents.application.runtime.tool_registry import BUILTIN_TOOL_NAMES

        tools = bt.build_agent_tools(
            _session(),
            agent=_agent(),
            tools=[],
            deps=MagicMock(),
            chatroom_id=_ROOM,
            activity_control=_control(_type()),
        )
        for tool in tools:
            assert tool.name in BUILTIN_TOOL_NAMES


def _tools_for(
    control: act.ActivityControlContext,
    *,
    agent: SimpleNamespace | None = None,
    sink: list[dict[str, Any]] | None = None,
    db: Any = None,
) -> dict[str, Any]:
    return {
        t.name: t
        for t in act.build_activity_control_tools(
            db or _session(),
            agent=agent or _agent(),
            control=control,
            event_sink=sink,
        )
    }


class TestStartActivity:
    async def test_it_records_the_granting_teacher_and_the_agent(self) -> None:
        """AC-7. The teacher is the answerable party and the recipient the
        facilitator's progress event is addressed to; the agent is what makes the
        round distinguishable."""
        activity_type = _type("unit2")
        agent = _agent()
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(activity_type), agent=agent, sink=sink)

        result = await tools["start_activity"].invoke({"activity_type_key": "unit2"})

        assert result.is_error is False
        call = _FakeActivitiesFacade.started[0]
        assert call["started_by_user_id"] == _GRANTER
        assert call["started_by_agent_id"] == agent.id
        assert call["activity_type_id"] == activity_type.id
        assert call["project_id"] == _PROJECT
        assert len(sink) == 1
        assert sink[0]["kind"] == act.EVENT_STARTED

    async def test_a_governance_refusal_is_a_readable_error_with_no_event(self) -> None:
        """AC-8. The policy gate runs before the insert, so no activation, audit row
        or broadcast is produced — the sink stays empty, which is what proves the
        room is never told."""
        _FakeActivitiesFacade.start_error = ActivityTypeViolatesPolicy(
            "expose_payload_to_agent", "the platform policy forbids it"
        )
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(_type("unit2")), sink=sink)

        result = await tools["start_activity"].invoke({"activity_type_key": "unit2"})

        assert result.is_error is True
        # The policy error carries a sentence naming the offending field, so it is
        # passed through rather than replaced.
        assert "the platform policy forbids it" in result.content
        assert sink == []

    async def test_an_already_running_activity_says_what_to_do_about_it(self) -> None:
        """AC-8's "readable" half. `ActivityAlreadyActive` carries only an activation
        UUID, so rendering the exception alone would tell the model
        "start_activity failed: 3f2a…" and leave it no way to infer that the fix is
        to end the running round first."""
        from contexts.activities.domain.errors import ActivityAlreadyActive

        _FakeActivitiesFacade.start_error = ActivityAlreadyActive(str(uuid.uuid4()))
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(_type("unit2")), sink=sink)

        result = await tools["start_activity"].invoke({"activity_type_key": "unit2"})

        assert result.is_error is True
        assert "End the current one" in result.content
        assert sink == []

    async def test_an_infrastructure_fault_fails_the_turn(self) -> None:
        """The catch-all must not swallow a DB fault: the tools write on the turn's
        own session, so hiding one keeps the turn buying provider tokens against a
        transaction whose reply can no longer be written."""
        from sqlalchemy.exc import OperationalError

        _FakeActivitiesFacade.start_error = OperationalError("SELECT 1", {}, Exception("boom"))
        tools = _tools_for(_control(_type("unit2")))

        with pytest.raises(OperationalError):
            await tools["start_activity"].invoke({"activity_type_key": "unit2"})

    async def test_an_unknown_key_is_refused_without_calling_the_service(self) -> None:
        tools = _tools_for(_control(_type("unit2")))

        result = await tools["start_activity"].invoke({"activity_type_key": "unit9"})

        assert result.is_error is True
        assert _FakeActivitiesFacade.started == []


class TestEndActivity:
    @staticmethod
    def _active(activity_type_id: uuid.UUID) -> ActivityActivation:
        return ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=_ROOM,
            activity_type_id=activity_type_id,
            started_by_user_id=_GRANTER,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
        )

    async def test_it_ends_the_rooms_active_round(self) -> None:
        activity_type = _type("unit2")
        agent = _agent()
        _FakeActivitiesFacade.active = self._active(activity_type.id)
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(activity_type), agent=agent, sink=sink)

        result = await tools["end_activity"].invoke({})

        assert result.is_error is False
        assert _FakeActivitiesFacade.ended[0]["ended_by_agent_id"] == agent.id
        assert _FakeActivitiesFacade.ended[0]["actor_user_id"] == _GRANTER
        assert sink[0]["kind"] == act.EVENT_ENDED

    async def test_it_refuses_a_round_of_a_type_outside_the_allowlist(self) -> None:
        """AC-9. An agent trusted with unit 2 must not be able to cut short a unit 4
        round the teacher started."""
        _FakeActivitiesFacade.active = self._active(uuid.uuid4())
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(_type("unit2")), sink=sink)

        result = await tools["end_activity"].invoke({})

        assert result.is_error is True
        assert "Only the teacher" in result.content
        assert _FakeActivitiesFacade.ended == []
        assert sink == []

    async def test_it_refuses_when_nothing_is_running(self) -> None:
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(_type("unit2")), sink=sink)

        result = await tools["end_activity"].invoke({})

        assert result.is_error is True
        assert _FakeActivitiesFacade.ended == []
        assert sink == []

    async def test_a_repeat_end_publishes_nothing(self) -> None:
        """A no-op end must not replay the event: the round already ended, and a
        second `activity.activation.ended` would tell the room so twice."""
        activity_type = _type("unit2")
        _FakeActivitiesFacade.active = self._active(activity_type.id)
        _FakeActivitiesFacade.end_transitioned = False
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(activity_type), sink=sink)

        result = await tools["end_activity"].invoke({})

        assert result.is_error is False
        assert sink == []


class TestNothingIsPublishedFromTheTool:
    """AC-10's first half, stated where it is structural rather than incidental."""

    async def test_a_successful_start_publishes_nothing_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from contexts.activities.interfaces import broadcast

        published: list[tuple[str, dict]] = []

        class _Publisher:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, event: str, payload: dict) -> None:
                published.append((event, payload))

        monkeypatch.setattr(broadcast, "Publisher", _Publisher)
        sink: list[dict[str, Any]] = []
        tools = _tools_for(_control(_type("unit2")), sink=sink)

        await tools["start_activity"].invoke({"activity_type_key": "unit2"})

        # The write is still inside the turn's open transaction here.
        assert published == []
        assert len(sink) == 1


class TestPostCommitDrain:
    """AC-10's second half, driven through the real ``_run_locked``.

    The tool-side assertions above prove nothing is published *early*; these prove
    the events do reach the room, that they reach it only after the turn's own
    commit, and that a turn that rolls back publishes nothing at all.
    """

    @staticmethod
    def _wire(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, list[tuple[str, dict, int]]]:
        from contexts.activities.interfaces import broadcast
        from tests.unit.turn_engine_fakes import make_agent, wire_engine

        agent = make_agent()
        engine, _trace = wire_engine(monkeypatch, agent, note={})

        # (event, payload, commits-at-emit-time) — the third element is what makes
        # "after the commit" an assertion rather than an assumption.
        published: list[tuple[str, dict, int]] = []

        class _Publisher:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, event: str, payload: dict) -> None:
                published.append((event, payload, engine._db.commits))

        monkeypatch.setattr(broadcast, "Publisher", _Publisher)
        monkeypatch.setattr(broadcast, "room_channel", lambda rid: f"ws:room:{rid}")

        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=_ROOM,
            activity_type_id=uuid.uuid4(),
            started_by_user_id=_GRANTER,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=agent.id,
        )

        async def _builtin_tools(
            agent_: object,
            agent_tools: object,
            *,
            chatroom_id: uuid.UUID | None = None,
            artifact_sink: list[dict[str, Any]] | None = None,
            activation_event_sink: list[dict[str, Any]] | None = None,
        ) -> list[Any]:
            # Stands in for the model actually calling `start_activity` mid-turn:
            # what the engine sees afterwards is a filled sink, however it got there.
            assert activation_event_sink is not None
            activation_event_sink.append(
                {"kind": act.EVENT_STARTED, "activation": activation, "activity_type": None}
            )
            return []

        engine._builtin_tools = _builtin_tools
        return engine, agent, published

    async def test_the_room_is_told_only_after_the_turn_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
        from tests.unit.turn_engine_fakes import run_locked

        engine, agent, published = self._wire(monkeypatch)

        async def _stream(**kw: Any) -> ToolLoopOutcome:
            return ToolLoopOutcome(text="started the round", rounds=1)

        engine._stream_with_tools = _stream
        result = await run_locked(engine, _ROOM, agent)

        assert result.status == "completed"
        started = [p for p in published if p[0] == "activity.activation.started"]
        assert len(started) == 1
        # Every commit the turn makes had already happened when the event went out
        # — which is the assertion, not merely "some commit had".
        assert started[0][2] == engine._db.commits
        assert engine._db.commits >= 2  # pre-stream + the reply itself
        assert started[0][1]["started_by_agent_name"] == agent.name

    async def test_a_silent_turn_still_tells_the_room(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pacing agent that starts a round and says nothing is an ordinary shape,
        not an edge case — the empty-reply branch commits and must drain too."""
        from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
        from tests.unit.turn_engine_fakes import run_locked

        engine, agent, published = self._wire(monkeypatch)

        async def _stream(**kw: Any) -> ToolLoopOutcome:
            return ToolLoopOutcome(text="", rounds=1)

        engine._stream_with_tools = _stream
        result = await run_locked(engine, _ROOM, agent)

        assert result.status == "skipped"
        assert [p[0] for p in published] == ["activity.activation.started"]

    async def test_a_granted_observers_round_still_reaches_the_room(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q-6 permits a granted observer, and states the asymmetry it creates: the
        agent is silent to the class ([R28.02]) while its activation is not. The
        observer branch has its own commit, so it needs its own drain."""
        import contexts.agents.application.runtime.turn_engine as te
        from contexts.activities.interfaces import broadcast
        from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
        from contexts.conversation.domain.models import ChatroomAgentRole
        from tests.unit.turn_engine_fakes import make_agent, run_locked, wire_engine

        agent = make_agent()
        engine, _trace = wire_engine(monkeypatch, agent, note={}, role=ChatroomAgentRole.OBSERVER)
        published: list[str] = []

        class _Publisher:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, event: str, payload: dict) -> None:
                published.append(event)

        monkeypatch.setattr(broadcast, "Publisher", _Publisher)
        monkeypatch.setattr(broadcast, "room_channel", lambda rid: f"ws:room:{rid}")

        class _ObservationService:
            def __init__(self, db: object) -> None:
                pass

            async def record(self, **kw: Any) -> SimpleNamespace:
                return SimpleNamespace(id=uuid.uuid4(), created_at=_NOW)

        monkeypatch.setattr(te, "ObservationService", _ObservationService)

        async def _emit_observation_event(*a: Any, **k: Any) -> None:
            return None

        engine._emit_observation_event = _emit_observation_event

        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=_ROOM,
            activity_type_id=uuid.uuid4(),
            started_by_user_id=_GRANTER,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=agent.id,
        )

        async def _builtin_tools(
            agent_: object,
            agent_tools: object,
            *,
            chatroom_id: uuid.UUID | None = None,
            artifact_sink: list[dict[str, Any]] | None = None,
            activation_event_sink: list[dict[str, Any]] | None = None,
        ) -> list[Any]:
            assert activation_event_sink is not None
            activation_event_sink.append(
                {"kind": act.EVENT_STARTED, "activation": activation, "activity_type": None}
            )
            return []

        engine._builtin_tools = _builtin_tools

        async def _stream(**kw: Any) -> ToolLoopOutcome:
            return ToolLoopOutcome(text="a quiet observation", rounds=1)

        engine._stream_with_tools = _stream
        result = await run_locked(engine, _ROOM, agent)

        assert result.status == "completed"
        assert published == ["activity.activation.started"]

    async def test_a_granted_observer_is_never_named_to_the_room(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[R28.10]. An observer is filtered out of every non-creator's agent
        roster and sends no messages, so the room channel — a blind relay that
        reaches chatroom guests — must not become the one thing that outs it. The
        round is still announced; only the initiator is withheld."""
        import contexts.agents.application.runtime.turn_engine as te
        from contexts.activities.interfaces import broadcast
        from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
        from contexts.conversation.domain.models import ChatroomAgentRole
        from tests.unit.turn_engine_fakes import make_agent, run_locked, wire_engine

        agent = make_agent()
        engine, _trace = wire_engine(monkeypatch, agent, note={}, role=ChatroomAgentRole.OBSERVER)
        payloads: list[dict] = []

        class _Publisher:
            def __init__(self, channel: str) -> None:
                pass

            async def emit(self, event: str, payload: dict) -> None:
                payloads.append(payload)

        monkeypatch.setattr(broadcast, "Publisher", _Publisher)
        monkeypatch.setattr(broadcast, "room_channel", lambda rid: f"ws:room:{rid}")

        class _ObservationService:
            def __init__(self, db: object) -> None:
                pass

            async def record(self, **kw: Any) -> SimpleNamespace:
                return SimpleNamespace(id=uuid.uuid4(), created_at=_NOW)

        monkeypatch.setattr(te, "ObservationService", _ObservationService)

        async def _emit_observation_event(*a: Any, **k: Any) -> None:
            return None

        engine._emit_observation_event = _emit_observation_event

        activation = ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=_ROOM,
            activity_type_id=uuid.uuid4(),
            started_by_user_id=_GRANTER,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
            started_by_agent_id=agent.id,
        )

        async def _builtin_tools(
            agent_: object,
            agent_tools: object,
            *,
            chatroom_id: uuid.UUID | None = None,
            artifact_sink: list[dict[str, Any]] | None = None,
            activation_event_sink: list[dict[str, Any]] | None = None,
        ) -> list[Any]:
            assert activation_event_sink is not None
            activation_event_sink.append(
                {"kind": act.EVENT_STARTED, "activation": activation, "activity_type": None}
            )
            return []

        engine._builtin_tools = _builtin_tools

        async def _stream(**kw: Any) -> ToolLoopOutcome:
            return ToolLoopOutcome(text="a quiet observation", rounds=1)

        engine._stream_with_tools = _stream
        await run_locked(engine, _ROOM, agent)

        # The round is announced...
        assert len(payloads) == 1
        # ...and carries neither field, so it is indistinguishable from a
        # teacher-started round on the wire.
        assert "started_by_agent_id" not in payloads[0]
        assert "started_by_agent_name" not in payloads[0]

    async def test_a_failed_turn_publishes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The failure path rolls back, so the activation no longer exists — telling
        a room about it would be telling it about a round nobody can join."""
        from contexts.agents.application.runtime.turn_engine import ToolLoopOutcome
        from tests.unit.turn_engine_fakes import run_locked

        engine, agent, published = self._wire(monkeypatch)

        async def _stream(**kw: Any) -> ToolLoopOutcome:
            raise RuntimeError("provider exploded")

        engine._stream_with_tools = _stream
        result = await run_locked(engine, _ROOM, agent)

        assert result.status == "failed"
        assert published == []
        assert engine._db.rollbacks >= 1
