"""The presentation-block schema, materialisation and serialiser ([R28.15]-[R28.19]).

Validation is asserted through ``schema_violations`` — the same function
``ToolRegistry.call`` runs before ``invoke`` — rather than through a second
validator, so what these tests prove is what the runtime actually enforces.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

import pytest

from contexts.activities.domain.models import (
    ActivityType,
    ActivityTypeScope,
    AttemptSummary,
    AttemptSummaryRow,
    FieldCoverage,
    FieldCoverageCell,
    MandalaGrid,
    ValidatorKind,
)
from contexts.agents.application.runtime import observation_blocks as ob
from contexts.agents.application.runtime.tool_registry import schema_violations

_ROOM = uuid.UUID("11111111-1111-1111-1111-111111111111")
_KEYS = ["mandala-9grid", "six-hats"]


def _schema(**overrides: list[str]) -> dict[str, Any]:
    kwargs: dict[str, list[str]] = {
        "coverage_keys": list(_KEYS),
        "mandala_keys": ["mandala-9grid"],
        "table_keys": list(_KEYS),
    }
    kwargs.update(overrides)
    return ob.build_blocks_schema(**kwargs)


def _violations(blocks: list[dict[str, Any]], **overrides: list[str]) -> list[str]:
    return schema_violations(_schema(**overrides), {"blocks": blocks})


def _activity_type(key: str, properties: dict[str, Any]) -> ActivityType:
    return ActivityType(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        key=key,
        name=f"Name of {key}",
        payload_schema={"type": "object", "properties": properties},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={},
        retention_days=None,
        version=1,
        created_at=dt.datetime(2026, 8, 24, tzinfo=dt.UTC),
        scope=ActivityTypeScope.PROJECT,
    )


class _FakeFacade:
    """Stands in for ``ActivitiesFacade`` at its source module.

    Patched there rather than on ``observation_blocks``: the facade is imported
    lazily inside ``materialise`` so it stays off the runtime import graph, which
    is the same seam ``test_activity_control_tools`` patches for the same reason.
    """

    coverage: ClassVar[FieldCoverage | None] = None
    grid: ClassVar[MandalaGrid | None] = None
    summary: ClassVar[AttemptSummary | None] = None
    calls: ClassVar[list[tuple[str, Any]]] = []

    def __init__(self, db: Any) -> None:
        pass

    async def field_coverage(self, *, chatroom_id, activity_type):
        _FakeFacade.calls.append(("field_coverage", activity_type.key))
        return _FakeFacade.coverage

    async def mandala_grid(self, *, chatroom_id, activity_type):
        _FakeFacade.calls.append(("mandala_grid", activity_type.key))
        return _FakeFacade.grid

    async def attempt_summary(self, *, chatroom_id, activity_type, limit):
        _FakeFacade.calls.append(("attempt_summary", limit))
        return _FakeFacade.summary


@pytest.fixture
def facade(monkeypatch: pytest.MonkeyPatch) -> type[_FakeFacade]:
    _FakeFacade.coverage = None
    _FakeFacade.grid = None
    _FakeFacade.summary = None
    _FakeFacade.calls = []
    monkeypatch.setattr("contexts.activities.interfaces.facade.ActivitiesFacade", _FakeFacade)
    return _FakeFacade


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


class TestSchema:
    def test_a_well_formed_array_of_every_kind_validates(self) -> None:
        assert (
            _violations(
                [
                    {"kind": "prose", "text": "what I saw"},
                    {
                        "kind": "key_points",
                        "title": "Three things",
                        "basis": "transcript",
                        "caveat": "only what was said out loud",
                        "points": [{"text": "a", "evidence": "b"}, {"text": "c"}],
                        "next_step": "ask the quiet half",
                    },
                    {
                        "kind": "timeline",
                        "basis": "recent_window",
                        "entries": [{"label": "10:05", "detail": "round opened"}],
                    },
                    {"kind": "field_coverage", "type_key": "mandala-9grid"},
                    {"kind": "mandala_grid", "type_key": "mandala-9grid", "title": "Grid"},
                    {"kind": "attempt_table", "limit": 10},
                ]
            )
            == []
        )

    def test_an_unknown_kind_is_named_back_to_the_model(self) -> None:
        """AC-4. A bare `oneOf` says only "not valid under any of the given
        schemas", which the model cannot act on."""
        messages = " ".join(_violations([{"kind": "bar_chart", "values": [1, 2]}]))
        assert "bar_chart" in messages
        assert "prose" in messages

    @pytest.mark.parametrize(
        "block",
        [
            {"kind": "field_coverage", "type_key": "mandala-9grid", "cells": [{"name": "a", "filled": 9}]},
            {"kind": "field_coverage", "type_key": "mandala-9grid", "submissions_counted": 30},
            {"kind": "mandala_grid", "type_key": "mandala-9grid", "rows": [[1, 2, 3]]},
            {"kind": "attempt_table", "rows": [{"subject_code": "u:1", "attempts": 9}]},
        ],
        ids=["cells", "denominator", "grid-rows", "table-rows"],
    )
    def test_a_computed_block_that_supplies_its_own_numbers_is_rejected(self, block: dict[str, Any]) -> None:
        """AC-6. This is what makes "the model cannot state a number" structural
        rather than a convention: the branch declares no value property and closes
        with additionalProperties: false."""
        assert _violations([block])

    @pytest.mark.parametrize(
        "block",
        [
            {"kind": "field_coverage", "type_key": "mandala-9grid"},
            {"kind": "mandala_grid", "type_key": "mandala-9grid"},
            {"kind": "attempt_table"},
        ],
        ids=list(ob.COMPUTED_KINDS),
    )
    @pytest.mark.parametrize("basis", ["transcript", ob.SERVER_FACTS], ids=["wrong", "even-the-right-one"])
    def test_a_computed_block_cannot_declare_its_own_basis(self, block: dict[str, Any], basis: str) -> None:
        """[R28.19]. The server stamps `server_facts`, so a computed block cannot
        be mislabelled by its caller — and stating the value the server would have
        stamped is refused on the same terms, because the point is that the label
        is not the caller's to assert.

        The accepting arm is asserted beside the rejecting one on purpose: this is
        the contract the shipped observer prompt has to *describe* (F-4), so a
        guard that only pinned the rejection would leave "and the same array
        without it is accepted" unstated, which is the half the prompt gets wrong.
        """
        assert _violations([block]) == []
        assert _violations([{**block, "basis": basis}])

    def test_a_type_key_outside_the_rooms_reachable_set_is_rejected(self) -> None:
        """AC-5."""
        assert _violations([{"kind": "field_coverage", "type_key": "some-other-room-worksheet"}])

    def test_the_mandala_enum_is_narrower_than_the_coverage_enum(self) -> None:
        """A nine-cell grid over a four-field worksheet is unrepresentable rather
        than handled."""
        assert _violations([{"kind": "mandala_grid", "type_key": "six-hats"}])
        assert _violations([{"kind": "field_coverage", "type_key": "six-hats"}]) == []

    def test_a_computed_kind_with_no_legal_value_is_not_offered_at_all(self) -> None:
        """An enum over nothing would let the model call a tool and then find no
        legal argument."""
        schema = ob.build_blocks_schema(coverage_keys=[], mandala_keys=[], table_keys=[])
        offered = schema["properties"]["blocks"]["items"]["properties"]["kind"]["enum"]
        assert offered == list(ob.NARRATIVE_KINDS)
        assert _violations(
            [{"kind": "field_coverage", "type_key": "mandala-9grid"}],
            coverage_keys=[],
            mandala_keys=[],
            table_keys=[],
        )

    def test_narrative_kinds_survive_a_room_with_no_activity_types(self) -> None:
        assert (
            _violations(
                [{"kind": "prose", "text": "still fine"}],
                coverage_keys=[],
                mandala_keys=[],
                table_keys=[],
            )
            == []
        )

    @pytest.mark.parametrize(
        "block",
        [
            {"kind": "key_points", "points": [{"text": "a"}]},
            {"kind": "timeline", "entries": [{"label": "a"}]},
        ],
        ids=["key_points", "timeline"],
    )
    def test_a_narrative_block_must_declare_its_basis(self, block: dict[str, Any]) -> None:
        """[R28.19]: not suppressible by omission either."""
        assert _violations([block])

    def test_the_basis_enum_is_closed(self) -> None:
        assert _violations([{"kind": "key_points", "basis": "my own judgement", "points": [{"text": "a"}]}])

    @pytest.mark.parametrize(
        "block",
        [
            {"kind": "prose", "text": "x" * 4001},
            {"kind": "key_points", "basis": "transcript", "points": []},
            {
                "kind": "key_points",
                "basis": "transcript",
                "points": [{"text": "a"}] * (ob.MAX_POINTS + 1),
            },
            {"kind": "key_points", "basis": "transcript", "points": [{"text": ""}]},
            {"kind": "attempt_table", "limit": 0},
            {"kind": "attempt_table", "limit": ob.MAX_TABLE_ROWS + 1},
        ],
        ids=["prose-too-long", "no-points", "too-many-points", "empty-point", "limit-0", "limit-31"],
    )
    def test_the_bounds_are_enforced(self, block: dict[str, Any]) -> None:
        assert _violations([block])

    def test_more_than_twelve_blocks_is_rejected(self) -> None:
        assert _violations([{"kind": "prose", "text": "x"}] * (ob.MAX_BLOCKS + 1))
        assert _violations([{"kind": "prose", "text": "x"}] * ob.MAX_BLOCKS) == []

    def test_the_top_level_argument_object_is_closed(self) -> None:
        assert schema_violations(_schema(), {"blocks": [], "content_md": "override"})


class TestStructuralViolations:
    def test_two_figures_of_one_kind_for_one_activity_are_refused(self) -> None:
        violations = ob.structural_violations(
            [
                {"kind": "field_coverage", "type_key": "a"},
                {"kind": "field_coverage", "type_key": "a"},
            ]
        )
        assert len(violations) == 1
        assert "field_coverage" in violations[0]

    def test_the_same_activity_may_carry_different_kinds(self) -> None:
        assert (
            ob.structural_violations(
                [
                    {"kind": "field_coverage", "type_key": "a"},
                    {"kind": "mandala_grid", "type_key": "a"},
                    {"kind": "attempt_table", "type_key": "a"},
                ]
            )
            == []
        )

    def test_two_unfiltered_attempt_tables_collide(self) -> None:
        assert ob.structural_violations([{"kind": "attempt_table"}, {"kind": "attempt_table"}])

    def test_narrative_blocks_may_repeat_freely(self) -> None:
        assert (
            ob.structural_violations([{"kind": "prose", "text": "a"}, {"kind": "prose", "text": "b"}]) == []
        )


# --------------------------------------------------------------------------- #
# Materialisation
# --------------------------------------------------------------------------- #


def _coverage(**over: Any) -> FieldCoverage:
    return FieldCoverage(
        type_key="mandala-9grid",
        type_name=over.get("type_name", "Unit 2"),
        submissions_counted=over.get("submissions_counted", 12),
        cells=over.get("cells", (FieldCoverageCell(name="home", title="家", filled=9),)),
    )


class TestMaterialise:
    async def test_narrative_blocks_pass_through_untouched(self, facade) -> None:
        blocks = [{"kind": "prose", "text": "hello"}]
        out, refusals = await ob.materialise(object(), chatroom_id=_ROOM, blocks=blocks, types_by_key={})
        assert out == blocks
        assert refusals == []
        assert facade.calls == []

    async def test_a_coverage_block_is_filled_in_and_stamped_server_facts(self, facade) -> None:
        facade.coverage = _coverage()
        at = _activity_type("mandala-9grid", {"home": {}})
        out, refusals = await ob.materialise(
            object(),
            chatroom_id=_ROOM,
            blocks=[{"kind": "field_coverage", "type_key": "mandala-9grid", "title": "Coverage"}],
            types_by_key={"mandala-9grid": at},
        )
        assert refusals == []
        block = out[0]
        assert block["basis"] == ob.SERVER_FACTS
        assert block["title"] == "Coverage"
        assert block["submissions_counted"] == 12
        assert block["cells"] == [{"name": "home", "title": "家", "filled": 9}]

    async def test_an_aggregate_with_nothing_to_count_refuses_the_whole_call(self, facade) -> None:
        """AC-6's refusal half: a chart of zeroes asserts nobody answered."""
        facade.coverage = None
        at = _activity_type("mandala-9grid", {"home": {}})
        out, refusals = await ob.materialise(
            object(),
            chatroom_id=_ROOM,
            blocks=[{"kind": "field_coverage", "type_key": "mandala-9grid"}],
            types_by_key={"mandala-9grid": at},
        )
        assert out == []
        assert len(refusals) == 1
        assert "mandala-9grid" in refusals[0]

    @pytest.mark.parametrize(
        ("supplied", "expected"),
        [
            (10, 10),
            (0, 1),
            (10**9, ob.MAX_TABLE_ROWS),
            (-5, 1),
            ("30", ob.DEFAULT_TABLE_ROWS),
            (True, ob.DEFAULT_TABLE_ROWS),
            (None, ob.DEFAULT_TABLE_ROWS),
        ],
        ids=["in-range", "zero", "huge", "negative", "string", "bool", "absent"],
    )
    async def test_the_row_limit_is_clamped_rather_than_trusted(
        self, facade, supplied: Any, expected: int
    ) -> None:
        """Security audit finding. The schema bounds `limit` to 1..30, but
        `schema_violations` fails **open** — a schema it cannot use costs the call
        its validation, not its turn — and that is the wrong thing to hang a SQL
        LIMIT on."""
        facade.summary = AttemptSummary(
            type_key=None, type_name=None, submissions_counted=1, rows=(), truncated=False
        )
        block: dict[str, Any] = {"kind": "attempt_table"}
        if supplied is not None:
            block["limit"] = supplied
        await ob.materialise(object(), chatroom_id=_ROOM, blocks=[block], types_by_key={})
        assert facade.calls == [("attempt_summary", expected)]

    async def test_an_attempt_table_defaults_its_limit(self, facade) -> None:
        facade.summary = AttemptSummary(
            type_key=None, type_name=None, submissions_counted=3, rows=(), truncated=False
        )
        await ob.materialise(object(), chatroom_id=_ROOM, blocks=[{"kind": "attempt_table"}], types_by_key={})
        assert facade.calls == [("attempt_summary", ob.DEFAULT_TABLE_ROWS)]

    async def test_an_attempt_table_carries_codes_and_the_truncation_flag(self, facade) -> None:
        facade.summary = AttemptSummary(
            type_key="six-hats",
            type_name="Unit 4",
            submissions_counted=40,
            rows=(
                AttemptSummaryRow(
                    subject_code="u:1a2b3c4d",
                    attempts=3,
                    submissions=5,
                    latest_outcome="invalid",
                    latest_error_class="too_few_filled",
                ),
            ),
            truncated=True,
        )
        out, _ = await ob.materialise(
            object(),
            chatroom_id=_ROOM,
            blocks=[{"kind": "attempt_table", "limit": 1}],
            types_by_key={"six-hats": _activity_type("six-hats", {})},
        )
        assert out[0]["truncated"] is True
        assert out[0]["rows"][0]["subject_code"] == "u:1a2b3c4d"

    async def test_a_type_key_not_in_the_mapping_is_refused_rather_than_queried(self, facade) -> None:
        """Unreachable through a schema-validating registry; the boundary must
        still hold if validation is ever loosened."""
        out, refusals = await ob.materialise(
            object(),
            chatroom_id=_ROOM,
            blocks=[{"kind": "field_coverage", "type_key": "borrowed"}],
            types_by_key={},
        )
        assert out == []
        assert len(refusals) == 1
        assert "borrowed" in refusals[0]
        assert facade.calls == []


class TestOversize:
    def test_a_normal_array_is_under_the_ceiling(self) -> None:
        assert ob.oversize_violation([{"kind": "prose", "text": "a paragraph"}]) is None

    def test_twelve_maximum_length_prose_blocks_are_refused(self) -> None:
        """The per-string caps do not bound the array on their own."""
        blocks = [{"kind": "prose", "text": "x" * 4000} for _ in range(ob.MAX_BLOCKS)]
        message = ob.oversize_violation(blocks)
        assert message is not None
        assert str(ob.MAX_BLOCKS_BYTES) in message


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


class TestSerialiser:
    def test_prose_is_its_own_text(self) -> None:
        assert ob.serialise_blocks([{"kind": "prose", "text": "just words"}]) == "just words"

    def test_key_points_render_as_a_heading_a_list_and_a_next_step(self) -> None:
        md = ob.serialise_blocks(
            [
                {
                    "kind": "key_points",
                    "title": "Three things",
                    "basis": "transcript",
                    "points": [{"text": "one", "evidence": "u:1a2b said so"}, {"text": "two"}],
                    "next_step": "ask again",
                }
            ]
        )
        assert "### Three things" in md
        assert "- one (u:1a2b said so)" in md
        assert "- two" in md
        assert "Suggested next step: ask again" in md

    def test_a_list_is_separated_from_what_follows_it(self) -> None:
        """Self-audit. A markdown paragraph directly beneath a list is a lazy
        continuation of its last item, so without the blank line the next step,
        the caveat and the basis sentence all folded into the final bullet — on
        the released copy too, where the basis label is the whole point."""
        md = ob.serialise_blocks(
            [
                {
                    "kind": "key_points",
                    "basis": "transcript",
                    "caveat": "a caveat",
                    "points": [{"text": "one"}],
                    "next_step": "ask again",
                }
            ]
        )
        lines = md.splitlines()
        assert lines[0] == "- one"
        assert lines[1] == ""
        assert lines[2] == "Suggested next step: ask again"
        assert lines[3] == ""
        assert lines[4] == "a caveat"

    def test_a_heading_is_separated_from_its_body(self) -> None:
        md = ob.serialise_blocks(
            [{"kind": "timeline", "title": "T", "basis": "transcript", "entries": [{"label": "a"}]}]
        )
        assert md.splitlines()[:3] == ["### T", "", "- a"]

    def test_no_run_of_blank_lines_and_no_trailing_one(self) -> None:
        md = ob.serialise_blocks([{"kind": "timeline", "basis": "transcript", "entries": [{"label": "a"}]}])
        assert "\n\n\n" not in md
        assert md == md.rstrip()

    def test_a_whitespace_only_prose_block_serialises_to_nothing(self) -> None:
        """The property the tool has to refuse on. `minLength: 1` accepts a space,
        and `schema_violations` strips `pattern`, so the schema cannot catch it."""
        assert ob.serialise_blocks([{"kind": "prose", "text": "   "}]) == ""

    def test_every_non_prose_block_carries_its_basis_sentence(self) -> None:
        """[R28.19]: the label travels with a released observation."""
        md = ob.serialise_blocks(
            [{"kind": "timeline", "basis": "recent_window", "entries": [{"label": "10:05"}]}]
        )
        assert ob.BASIS_SENTENCES["recent_window"] in md

    def test_the_caveat_is_always_serialised(self) -> None:
        md = ob.serialise_blocks(
            [
                {
                    "kind": "timeline",
                    "basis": "transcript",
                    "caveat": "the last ten minutes only",
                    "entries": [{"label": "a"}],
                }
            ]
        )
        assert "the last ten minutes only" in md

    def test_a_computed_block_renders_a_table_and_a_submissions_denominator(self) -> None:
        """AC-9: submissions counted, never a share of a class."""
        md = ob.serialise_blocks(
            [
                {
                    "kind": "field_coverage",
                    "basis": "server_facts",
                    "type_key": "mandala-9grid",
                    "submissions_counted": 12,
                    "cells": [{"name": "home", "title": "家", "filled": 9}],
                }
            ]
        )
        assert "| Field | Answered |" in md
        assert "| 家 | 9 |" in md
        assert "12 submissions counted." in md
        assert "%" not in md

    def test_one_submission_is_not_pluralised(self) -> None:
        md = ob.serialise_blocks([{"kind": "field_coverage", "submissions_counted": 1, "cells": []}])
        assert "1 submission counted." in md

    def test_a_grid_renders_three_rows_of_three(self) -> None:
        cells = [{"name": f"c{i}", "title": f"T{i}", "filled": i} for i in range(9)]
        md = ob.serialise_blocks(
            [
                {
                    "kind": "mandala_grid",
                    "basis": "server_facts",
                    "submissions_counted": 4,
                    "rows": [cells[0:3], cells[3:6], cells[6:9]],
                }
            ]
        )
        body = [line for line in md.splitlines() if line.startswith("| T")]
        assert len(body) == 3
        assert "T0 (0)" in body[0]

    def test_an_attempt_table_shows_the_error_class_the_way_the_context_block_does(self) -> None:
        md = ob.serialise_blocks(
            [
                {
                    "kind": "attempt_table",
                    "basis": "server_facts",
                    "submissions_counted": 5,
                    "truncated": True,
                    "rows": [
                        {
                            "subject_code": "u:1a2b3c4d",
                            "attempts": 3,
                            "submissions": 5,
                            "latest_outcome": "invalid",
                            "latest_error_class": "too_few_filled",
                        }
                    ],
                }
            ]
        )
        assert "| u:1a2b3c4d | 3 | 5 | invalid [too_few_filled] |" in md
        assert "this room has more" in md

    def test_a_pipe_in_a_field_title_cannot_split_the_row(self) -> None:
        """Titles are owner-authored; an unescaped `|` would shift every value
        after it into the wrong column."""
        md = ob.serialise_blocks(
            [
                {
                    "kind": "field_coverage",
                    "submissions_counted": 1,
                    "cells": [{"name": "a", "title": "left | right", "filled": 1}],
                }
            ]
        )
        row = next(line for line in md.splitlines() if "left" in line)
        assert row == "| left \\| right | 1 |"

    def test_a_newline_in_agent_text_cannot_open_a_block_of_its_own(self) -> None:
        md = ob.serialise_blocks(
            [
                {
                    "kind": "key_points",
                    "basis": "transcript",
                    "points": [{"text": "one\n\n### Fake heading"}],
                }
            ]
        )
        assert "- one ### Fake heading" in md
        assert "\n### Fake heading" not in md

    def test_an_unknown_kind_is_omitted_rather_than_guessed_at(self) -> None:
        """A kind this serialiser cannot render honestly would otherwise reach the
        room as unlabelled text on release. The stored array still keeps it."""
        md = ob.serialise_blocks(
            [{"kind": "prose", "text": "kept"}, {"kind": "from_the_future", "title": "?"}]
        )
        assert md == "kept"

    def test_the_serialisation_is_stable_across_calls(self) -> None:
        blocks: list[dict[str, Any]] = [
            {"kind": "prose", "text": "a"},
            {"kind": "timeline", "basis": "transcript", "entries": [{"label": "b", "detail": "c"}]},
        ]
        assert ob.serialise_blocks(blocks) == ob.serialise_blocks(blocks)

    def test_blocks_are_joined_in_array_order(self) -> None:
        md = ob.serialise_blocks(
            [
                {"kind": "prose", "text": "first"},
                {"kind": "timeline", "basis": "transcript", "entries": [{"label": "middle"}]},
                {"kind": "prose", "text": "last"},
            ]
        )
        assert md.index("first") < md.index("middle") < md.index("last")

    def test_an_empty_array_serialises_to_an_empty_string(self) -> None:
        assert ob.serialise_blocks([]) == ""
