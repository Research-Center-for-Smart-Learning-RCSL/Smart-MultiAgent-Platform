"""``present_observation``: who gets it, what its enum may hold, what it records.

Mirrors ``test_activity_control_tools.py``'s structure, because the two tools make
the same safety argument from the same shape: the only path from model output to a
rendered surface is a schema-validated call over server-built enums.

Covers AC-2 (a normal-role binding is never offered it, in any room), AC-5 (the
enum is exactly the room's reachable set), and the refusal/record behaviour behind
AC-4 and AC-6.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.activities.domain.models import (
    ActivityType,
    ActivityTypeScope,
    AttemptSummary,
    FieldCoverage,
    FieldCoverageCell,
    ValidatorKind,
)
from contexts.agents.application.runtime import builtin_tools as bt
from contexts.agents.application.runtime import observation_blocks as ob
from contexts.agents.application.runtime import observer_tools as ot

_NOW = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)
_ROOM = uuid.uuid4()
_PROJECT = uuid.uuid4()


def _session() -> AsyncMock:
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.info = {}
    return db


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=_PROJECT, name="AA")


def _type(key: str, *, fields: int = 4) -> ActivityType:
    return ActivityType(
        id=uuid.uuid4(),
        project_id=_PROJECT,
        key=key,
        name=f"Name of {key}",
        payload_schema={
            "type": "object",
            "properties": {f"f{i}": {"type": "string"} for i in range(fields)},
        },
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count_coverage", "min_filled": 1},
        retention_days=None,
        version=1,
        created_at=_NOW,
        scope=ActivityTypeScope.PROJECT,
    )


def _presentation(*types: ActivityType) -> ot.ObservationPresentationContext:
    return ot.ObservationPresentationContext(
        chatroom_id=_ROOM,
        project_id=_PROJECT,
        types_by_key={t.key: t for t in types},
    )


class _FakeConversationFacade:
    project_id: ClassVar[uuid.UUID | None] = _PROJECT
    error: ClassVar[Exception | None] = None

    def __init__(self, db: object) -> None:
        pass

    async def project_id_for_chatroom(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
        if _FakeConversationFacade.error is not None:
            raise _FakeConversationFacade.error
        return _FakeConversationFacade.project_id


class _FakeActivitiesFacade:
    types: ClassVar[list[ActivityType]] = []
    coverage: ClassVar[FieldCoverage | None] = None
    summary: ClassVar[AttemptSummary | None] = None
    list_error: ClassVar[Exception | None] = None

    def __init__(self, db: object) -> None:
        pass

    async def list_types(self, project_id: uuid.UUID) -> list[ActivityType]:
        if _FakeActivitiesFacade.list_error is not None:
            raise _FakeActivitiesFacade.list_error
        return list(_FakeActivitiesFacade.types)

    async def field_coverage(self, *, chatroom_id, activity_type):
        return _FakeActivitiesFacade.coverage

    async def mandala_grid(self, *, chatroom_id, activity_type):
        return None

    async def attempt_summary(self, *, chatroom_id, activity_type, limit):
        return _FakeActivitiesFacade.summary


@pytest.fixture(autouse=True)
def facades(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeConversationFacade.project_id = _PROJECT
    _FakeConversationFacade.error = None
    _FakeActivitiesFacade.types = []
    _FakeActivitiesFacade.coverage = None
    _FakeActivitiesFacade.summary = None
    _FakeActivitiesFacade.list_error = None
    monkeypatch.setattr(ot, "ConversationFacade", _FakeConversationFacade)
    # One seam covers both the resolver and the block materialiser: each imports
    # the facade lazily from this module path.
    monkeypatch.setattr("contexts.activities.interfaces.facade.ActivitiesFacade", _FakeActivitiesFacade)


# --------------------------------------------------------------------------- #
# Resolution — the role is the whole authorization (AC-2)
# --------------------------------------------------------------------------- #


class TestResolution:
    async def test_a_normal_role_binding_resolves_nothing(self) -> None:
        """AC-2, at the resolver. `is_observer` is threaded from `run_turn`, which
        is where the binding role is read."""
        assert (
            await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=False)
        ) is None

    async def test_a_headless_turn_with_no_room_resolves_nothing(self) -> None:
        assert (
            await ot.resolve_observation_presentation(_session(), chatroom_id=None, is_observer=True)
        ) is None

    async def test_an_observer_turn_resolves_the_rooms_reachable_types(self) -> None:
        _FakeActivitiesFacade.types = [_type("unit2"), _type("unit4")]
        ctx = await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=True)
        assert ctx is not None
        assert set(ctx.types_by_key) == {"unit2", "unit4"}
        assert ctx.project_id == _PROJECT

    async def test_a_room_with_no_reachable_types_still_gets_the_tool(self) -> None:
        """The opposite reading to `resolve_activity_control`'s empty allowlist,
        and deliberately so: the narrative kinds do not need a type, so the model
        still has a legal call."""
        _FakeActivitiesFacade.types = []
        ctx = await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=True)
        assert ctx is not None
        assert ctx.types_by_key == {}

    async def test_an_unresolvable_project_resolves_nothing(self) -> None:
        _FakeConversationFacade.project_id = None
        assert (
            await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=True)
        ) is None

    @pytest.mark.parametrize("where", ["conversation", "activities"])
    async def test_any_exception_fails_closed_and_is_logged(
        self, where: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`TurnEngine._builtin_tools` swallows every assembly exception into "no
        tools at all", so a bug in here is silent unless the resolver says so."""
        if where == "conversation":
            _FakeConversationFacade.error = RuntimeError("db down")
        else:
            _FakeActivitiesFacade.list_error = RuntimeError("db down")
        with caplog.at_level("WARNING"):
            assert (
                await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=True)
            ) is None
        assert "offering no tool" in caplog.text

    async def test_two_types_sharing_a_key_get_distinct_enum_values(self) -> None:
        """[R30.02] permits a project type and an opted-in platform type to share a
        key; an ambiguous enum would let the model name a value resolving to two
        different worksheets."""
        _FakeActivitiesFacade.types = [_type("unit2"), _type("unit2")]
        ctx = await ot.resolve_observation_presentation(_session(), chatroom_id=_ROOM, is_observer=True)
        assert ctx is not None
        assert len(ctx.types_by_key) == 2


# --------------------------------------------------------------------------- #
# Assembly — what the built tool advertises (AC-2, AC-5)
# --------------------------------------------------------------------------- #


class TestAssembly:
    def test_a_normal_role_turn_is_never_offered_the_tool(self) -> None:
        """AC-2, at the assembly seam — including in a room holding an
        activity-control grant, which is a different authority entirely."""
        tools = bt.build_agent_tools(
            _session(), agent=_agent(), tools=[], deps=MagicMock(), observation_presentation=None
        )
        assert ot.TOOL_NAME not in {t.name for t in tools}

    def test_an_observer_turn_is_offered_exactly_one_tool(self) -> None:
        tools = bt.build_agent_tools(
            _session(),
            agent=_agent(),
            tools=[],
            deps=MagicMock(),
            observation_presentation=_presentation(_type("unit2")),
        )
        assert [t.name for t in tools] == [ot.TOOL_NAME]

    def test_the_type_enum_is_exactly_the_rooms_reachable_set(self) -> None:
        """AC-5."""
        tool = ot.build_present_observation_tool(
            _session(), presentation=_presentation(_type("unit2"), _type("unit4"))
        )
        branches = tool.input_schema["properties"]["blocks"]["items"]["oneOf"]
        coverage = next(b for b in branches if b["properties"]["kind"]["enum"] == [ob.FIELD_COVERAGE])
        assert coverage["properties"]["type_key"]["enum"] == ["unit2", "unit4"]

    def test_only_a_nine_property_type_reaches_the_mandala_enum(self) -> None:
        tool = ot.build_present_observation_tool(
            _session(),
            presentation=_presentation(_type("nine", fields=9), _type("four", fields=4)),
        )
        branches = tool.input_schema["properties"]["blocks"]["items"]["oneOf"]
        grid = next(b for b in branches if b["properties"]["kind"]["enum"] == [ob.MANDALA_GRID])
        assert grid["properties"]["type_key"]["enum"] == ["nine"]

    def test_no_nine_property_type_means_no_grid_branch_at_all(self) -> None:
        tool = ot.build_present_observation_tool(
            _session(), presentation=_presentation(_type("four", fields=4))
        )
        kinds = tool.input_schema["properties"]["blocks"]["items"]["properties"]["kind"]["enum"]
        assert ob.MANDALA_GRID not in kinds
        assert ob.FIELD_COVERAGE in kinds

    def test_the_description_names_the_activities_and_the_server_split(self) -> None:
        tool = ot.build_present_observation_tool(_session(), presentation=_presentation(_type("unit2")))
        assert "unit2" in tool.description
        assert "not yours" in tool.description


# --------------------------------------------------------------------------- #
# Invocation — the sink, the refusals, the "last call wins" rule
# --------------------------------------------------------------------------- #


def _coverage() -> FieldCoverage:
    return FieldCoverage(
        type_key="unit2",
        type_name="Name of unit2",
        submissions_counted=7,
        cells=(FieldCoverageCell(name="f0", title="F0", filled=5),),
    )


class TestInvocation:
    async def test_a_valid_call_writes_the_materialised_blocks_into_the_sink(self) -> None:
        _FakeActivitiesFacade.coverage = _coverage()
        sink: list[dict[str, Any]] = []
        tool = ot.build_present_observation_tool(
            _session(), presentation=_presentation(_type("unit2")), block_sink=sink
        )
        result = await tool.invoke(
            {
                "blocks": [
                    {"kind": "prose", "text": "what I saw"},
                    {"kind": "field_coverage", "type_key": "unit2"},
                ]
            }
        )
        assert result.is_error is False
        assert [b["kind"] for b in sink] == ["prose", "field_coverage"]
        assert sink[1]["submissions_counted"] == 7
        assert sink[1]["basis"] == ob.SERVER_FACTS

    async def test_the_last_call_replaces_the_previous_one(self) -> None:
        """[R28.16]. Appending would stack a model's draft and its revision."""
        sink: list[dict[str, Any]] = []
        tool = ot.build_present_observation_tool(_session(), presentation=_presentation(), block_sink=sink)
        await tool.invoke({"blocks": [{"kind": "prose", "text": "draft"}]})
        result = await tool.invoke({"blocks": [{"kind": "prose", "text": "final"}]})
        assert [b["text"] for b in sink] == ["final"]
        assert "replaces" in result.content

    async def test_a_duplicate_computed_block_records_nothing(self) -> None:
        _FakeActivitiesFacade.coverage = _coverage()
        sink: list[dict[str, Any]] = []
        tool = ot.build_present_observation_tool(
            _session(), presentation=_presentation(_type("unit2")), block_sink=sink
        )
        result = await tool.invoke(
            {
                "blocks": [
                    {"kind": "field_coverage", "type_key": "unit2"},
                    {"kind": "field_coverage", "type_key": "unit2"},
                ]
            }
        )
        assert result.is_error is True
        assert "Nothing was recorded" in result.content
        assert sink == []

    async def test_an_aggregate_with_no_data_refuses_the_whole_call(self) -> None:
        """Partial success would leave the model unable to tell what the teacher
        will see, so a refusal is all-or-nothing."""
        _FakeActivitiesFacade.coverage = None
        sink: list[dict[str, Any]] = []
        tool = ot.build_present_observation_tool(
            _session(), presentation=_presentation(_type("unit2")), block_sink=sink
        )
        result = await tool.invoke(
            {
                "blocks": [
                    {"kind": "prose", "text": "kept in the retry"},
                    {"kind": "field_coverage", "type_key": "unit2"},
                ]
            }
        )
        assert result.is_error is True
        assert "unit2" in result.content
        assert sink == []

    async def test_an_oversize_array_is_refused_with_the_limit_named(self) -> None:
        sink: list[dict[str, Any]] = []
        tool = ot.build_present_observation_tool(_session(), presentation=_presentation(), block_sink=sink)
        result = await tool.invoke(
            {"blocks": [{"kind": "prose", "text": "x" * 4000} for _ in range(ob.MAX_BLOCKS)]}
        )
        assert result.is_error is True
        assert str(ob.MAX_BLOCKS_BYTES) in result.content
        assert sink == []

    async def test_a_sinkless_tool_still_validates_and_reports(self) -> None:
        """Only correct for a caller with no post-stream seam, and it must not
        blow up when one builds it that way."""
        tool = ot.build_present_observation_tool(_session(), presentation=_presentation())
        result = await tool.invoke({"blocks": [{"kind": "prose", "text": "fine"}]})
        assert result.is_error is False

    async def test_the_tool_commits_nothing(self) -> None:
        db = _session()
        _FakeActivitiesFacade.coverage = _coverage()
        tool = ot.build_present_observation_tool(
            db, presentation=_presentation(_type("unit2")), block_sink=[]
        )
        await tool.invoke({"blocks": [{"kind": "field_coverage", "type_key": "unit2"}]})
        db.commit.assert_not_awaited()
