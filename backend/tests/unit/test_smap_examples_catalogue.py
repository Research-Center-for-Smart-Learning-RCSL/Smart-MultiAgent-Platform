"""The course catalogue: the shipped content, pinned field-for-field, plus the
loader's rejection rules.

The expected values below were written *before* the Python-constants -> JSON
transcription, precisely so they could catch the one defect that transcription
is most likely to introduce: a prompt string altered in transit. They are
spelled out literally rather than derived from a loop, so they stay independent
of however the production side happens to build the schema. Edit them only when
the course content is deliberately changing.

Imports still go through ``smap.examples._catalogue``: the parser moved into the
activities context, and that module is now a re-export. Reading these tests
through the shim is deliberate -- it is the import path the seeder CLI and every
operator script use, so a break in the re-export shows up here.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.domain.models import ValidatorKind
from smap.examples._catalogue import (
    CourseFileInvalid,
    available_courses,
    load_course,
)

# At import as well as per test: the module-level SHIPPED_TYPES below parses a real
# course during collection, before any fixture runs.
register_first_party_validators()


@pytest.fixture(autouse=True)
def _registered_validators() -> None:
    """The loader checks a course's validator_config against the in-process
    registry, which a first-party site populates (app startup, or the CLI's own
    call). Re-registering per test keeps this file independent of whichever other
    module last called ``clear_registry()`` in a teardown."""
    register_first_party_validators()


# The four types below transcribe the thesis worksheets (appendix 一, PDF pages 118
# and 126) rather than a paraphrase of them. Cell themes, hat order, and hat
# descriptors are the worksheet's own; `x-order` carries the order the worksheet
# fixes, because the stored schema is jsonb and object key order does not survive
# it ([R30.36]).
MANDALA: dict[str, Any] = {
    "key": "mandala-9grid",
    "name": "單元二 時空旅人（曼陀羅九宮格）",
    "validator_kind": "in_process",
    "validator_config": {"validator_id": "filled_count_coverage", "min_filled": 4},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "home": {"type": "string", "title": "家", "x-order": 1},
            "work": {"type": "string", "title": "工作", "x-order": 2},
            "abilities": {"type": "string", "title": "具備能力", "x-order": 3},
            "appearance": {"type": "string", "title": "外貌", "x-order": 4},
            "center": {
                "type": "string",
                "title": "30 歲的我會有什麼改變呢？",
                "description": "想像 30 歲的自己過的一天，和 13 歲的今天有什麼不同。",
                "x-order": 5,
            },
            "leisure": {"type": "string", "title": "休閒娛樂", "x-order": 6},
            "message_to_self": {"type": "string", "title": "想對 30 歲的自己說…", "x-order": 7},
            "free": {
                "type": "string",
                "title": "自由發揮",
                "description": "這一格沒有主題，由你自己決定要寫什麼。",
                "x-order": 8,
            },
            "relationships": {"type": "string", "title": "人際關係", "x-order": 9},
        },
        # The worksheet's centre cell is a printed question, not a blank, so
        # nothing in this unit is individually mandatory; `min_filled` carries the
        # completeness floor instead.
        "required": [],
    },
}

NEXT_STEPS: dict[str, Any] = {
    "key": "time-traveler-next-steps",
    "name": "單元二 為了與你相遇",
    "validator_kind": "in_process",
    # Deliberately NOT the coverage variant. A one-field worksheet's coverage
    # figure only ever reads "1/1 fields answered", and adopting it would cost the
    # agents the answer text `2026-08-24-example-agents-quote-unit-two` made
    # quotable — see D-7 of the presentation-blocks dossier.
    "validator_config": {"validator_id": "filled_count", "min_filled": 1},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "next_steps": {
                "type": "string",
                "title": "若要讓我更接近想像中的生活，現在的我需要學習的可能有",
                "description": "回顧剛才的曼陀羅，寫下現在就能開始的努力。",
                "x-order": 1,
            },
        },
        "required": ["next_steps"],
    },
}

THREE_EMOTIONS: dict[str, Any] = {
    "key": "emotion-desk-three-emotions",
    "name": "單元四 情緒播報台（三種情緒）",
    "validator_kind": "in_process",
    "validator_config": {"validator_id": "filled_count_coverage", "min_filled": 2},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "emotion_1": {
                "type": "string",
                "title": "情緒一",
                "description": "生活中最常出現的三種情緒之一。",
                "x-order": 1,
            },
            "emotion_1_reason": {"type": "string", "title": "情緒一最近一次出現的原因", "x-order": 2},
            "emotion_2": {"type": "string", "title": "情緒二", "x-order": 3},
            "emotion_2_reason": {"type": "string", "title": "情緒二最近一次出現的原因", "x-order": 4},
            "emotion_3": {"type": "string", "title": "情緒三", "x-order": 5},
            "emotion_3_reason": {"type": "string", "title": "情緒三最近一次出現的原因", "x-order": 6},
        },
        "required": ["emotion_1"],
    },
}

SIX_HATS: dict[str, Any] = {
    "key": "six-hats-emotion-desk",
    "name": "單元四 情緒列車（六頂思考帽）",
    "validator_kind": "in_process",
    "validator_config": {"validator_id": "filled_count_coverage", "min_filled": 3},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "title": "事件",
                "description": "一件最近或曾經讓自己困擾的事情。",
                "x-order": 1,
            },
            "hat_white": {"type": "string", "title": "白帽", "description": "中立、客觀、事實", "x-order": 2},
            "hat_red": {"type": "string", "title": "紅帽", "description": "情緒、直覺、預感", "x-order": 3},
            "hat_black": {"type": "string", "title": "黑帽", "description": "悲觀、負面、謹慎", "x-order": 4},
            "hat_yellow": {
                "type": "string",
                "title": "黃帽",
                "description": "樂觀、正面、積極",
                "x-order": 5,
            },
            "hat_blue": {"type": "string", "title": "藍帽", "description": "指揮、控制、結論", "x-order": 6},
        },
        "required": ["event"],
    },
}

CREATIVE_THINKING_TYPES: tuple[dict[str, Any], ...] = (MANDALA, NEXT_STEPS, THREE_EMOTIONS, SIX_HATS)

SHIPPED_TYPES = load_course("creative-thinking").activity_types


def as_dicts(activity_types: Any) -> list[dict[str, Any]]:
    """Course types as plain dicts, so the pin does not depend on their class."""
    return [dataclasses.asdict(t) for t in activity_types]


class TestShippedCourseContent:
    """G-1: the exact seeded values, not merely their shape.

    Without this, a prompt string could be silently altered during the move and
    every other test in the suite would still pass.
    """

    def test_the_course_is_exactly_the_pinned_units(self) -> None:
        assert as_dicts(SHIPPED_TYPES) == list(CREATIVE_THINKING_TYPES)

    @pytest.mark.parametrize(
        ("index", "expected"),
        enumerate(CREATIVE_THINKING_TYPES),
        ids=[t["key"] for t in CREATIVE_THINKING_TYPES],
    )
    def test_each_unit_matches_field_for_field(self, index: int, expected: dict[str, Any]) -> None:
        """Same assertion split per unit, so a failure names the unit that drifted."""
        assert dataclasses.asdict(SHIPPED_TYPES[index]) == expected

    def test_every_property_declares_a_contiguous_render_order(self) -> None:
        """AC-3: `x-order` is what fixes render order, and it has to be complete.

        Object key order cannot carry it: `activity_types.payload_schema` is jsonb,
        which normalises keys by length then bytewise
        (`tests/integration/test_activity_schema_key_order.py` pins that against a
        real database). A property missing an `x-order` sorts after every declared
        one, and two sharing a value tie -- both would reshuffle a worksheet
        silently, so 1..n with no gaps and no repeats is the invariant.
        """
        for activity_type in SHIPPED_TYPES:
            orders = [p["x-order"] for p in activity_type.payload_schema["properties"].values()]
            assert sorted(orders) == list(range(1, len(orders) + 1)), activity_type.key

    def test_the_mandala_grid_reads_as_the_worksheet(self) -> None:
        """The 3x3 the plugin builds: it drops `center`, then splices it back at
        index 4 (`MandalaGrid.vue`), so declared order 1..9 lands as the worksheet
        prints it (thesis appendix 一, PDF p.118)."""
        properties = MANDALA["payload_schema"]["properties"]
        by_order = sorted(properties, key=lambda name: properties[name]["x-order"])
        ring = [name for name in by_order if name != "center"]
        grid = [*ring[:4], "center", *ring[4:]]

        assert grid == [
            "home",
            "work",
            "abilities",
            "appearance",
            "center",
            "leisure",
            "message_to_self",
            "free",
            "relationships",
        ]

    def test_the_hats_follow_the_worksheet_order(self) -> None:
        """白, 紅, 黑, 黃, 藍 -- the 情緒列車 table's own column order (PDF p.126).

        Pinned separately from the field-for-field comparison because this is the
        one the shipped file got wrong: it had 黃 and 黑 transposed, behind a
        rationale claiming the thesis fixed no sequence.
        """
        properties = SIX_HATS["payload_schema"]["properties"]
        by_order = sorted(properties, key=lambda name: properties[name]["x-order"])

        assert by_order == ["event", "hat_white", "hat_red", "hat_black", "hat_yellow", "hat_blue"]

    def test_the_course_carries_its_provenance(self) -> None:
        course = load_course("creative-thinking")

        assert course.title
        assert "Ke Pei-jung" in course.source


class TestEveryShippedCourse:
    @pytest.mark.parametrize("course_key", available_courses())
    def test_loads_and_validates(self, course_key: str) -> None:
        """Runs over the whole catalogue, so a new course file is covered by
        adding the file — no test edit."""
        course = load_course(course_key)

        assert course.course_key == course_key
        assert course.activity_types


def _course_document() -> dict[str, Any]:
    """A minimal course that loads cleanly; each test breaks one thing in it."""
    return {
        "course_key": "fixture-course",
        "title": "Fixture course",
        "source": "test fixture",
        "activity_types": [
            {
                "key": "unit-one",
                "name": "單元一 測試",
                "validator_kind": "in_process",
                "validator_config": {"validator_id": "filled_count", "min_filled": 1},
                "retention_days": None,
                "expose_payload_to_agent": True,
                "echo_includes_content": False,
                "payload_schema": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "title": "答案", "description": "寫下你的想法。"},
                        "reason": {"type": "string", "title": "理由"},
                    },
                    "required": ["answer"],
                },
            }
        ],
    }


def _write_course(root: Path, document: dict[str, Any], *, name: str | None = None) -> Path:
    """Write a course file as UTF-8 bytes, independent of the platform default."""
    path = root / (name or f"{document['course_key']}.json")
    path.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
    return path


class TestLoaderAcceptsAWellFormedCourse:
    def test_loads_a_course_from_any_directory(self, tmp_path: Path) -> None:
        """AC-7: a course is a data file, so an arbitrary directory loads the same."""
        _write_course(tmp_path, _course_document())

        course = load_course("fixture-course", root=tmp_path)

        assert course.course_key == "fixture-course"
        assert course.title == "Fixture course"
        assert [t.key for t in course.activity_types] == ["unit-one"]
        assert course.activity_types[0].validator_kind is ValidatorKind.IN_PROCESS
        assert course.activity_types[0].retention_days is None

    def test_non_ascii_text_round_trips(self, tmp_path: Path) -> None:
        """AC-5: UTF-8 is pinned in the loader, not inherited from the host locale.

        The file is written as UTF-8 bytes while the platform default on a Windows
        host is not UTF-8, so a loader that omitted `encoding=` fails here rather
        than mojibaking a prompt in front of a class.
        """
        _write_course(tmp_path, _course_document())

        activity_type = load_course("fixture-course", root=tmp_path).activity_types[0]

        assert activity_type.name == "單元一 測試"
        assert activity_type.payload_schema["properties"]["answer"]["title"] == "答案"
        assert activity_type.payload_schema["properties"]["answer"]["description"] == "寫下你的想法。"

    def test_available_courses_lists_the_json_files(self, tmp_path: Path) -> None:
        _write_course(tmp_path, _course_document())
        _write_course(tmp_path, {**_course_document(), "course_key": "another-course"})
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

        assert available_courses(root=tmp_path) == ("another-course", "fixture-course")


class TestLoaderRejectsAMalformedCourse:
    """AC-4. Every message names the file and the offending key: the operator
    seeing it is hand-editing JSON and needs to know where to look."""

    def _load_broken(self, tmp_path: Path, mutate: Any) -> str:
        document = _course_document()
        mutate(document)
        _write_course(tmp_path, document, name="fixture-course.json")
        with pytest.raises(CourseFileInvalid) as excinfo:
            load_course("fixture-course", root=tmp_path)
        message = str(excinfo.value)
        assert "fixture-course.json" in message, message
        return message

    def test_a_missing_required_field(self, tmp_path: Path) -> None:
        def drop_name(doc: dict[str, Any]) -> None:
            del doc["activity_types"][0]["name"]

        message = self._load_broken(tmp_path, drop_name)
        assert "name" in message
        assert "activity_types[0]" in message

    def test_a_missing_top_level_field(self, tmp_path: Path) -> None:
        message = self._load_broken(tmp_path, lambda doc: doc.pop("source"))
        assert "source" in message

    def test_an_unknown_field(self, tmp_path: Path) -> None:
        """A typo'd flag name must not fall through to a default.

        `expose_payload_to_agents` defaulting to true would silently send
        participant text to an LLM provider.
        """

        def typo(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["expose_payload_to_agents"] = False

        message = self._load_broken(tmp_path, typo)
        assert "expose_payload_to_agents" in message

    def test_an_unknown_validator_kind(self, tmp_path: Path) -> None:
        def bad_kind(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_kind"] = "in-process"

        message = self._load_broken(tmp_path, bad_kind)
        assert "validator_kind" in message
        assert "in_process" in message

    def test_a_malformed_payload_schema(self, tmp_path: Path) -> None:
        def bad_schema(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["payload_schema"] = {
                "type": "object",
                "properties": {"answer": {"type": "not-a-json-schema-type"}},
            }

        message = self._load_broken(tmp_path, bad_schema)
        assert "payload_schema" in message

    def test_an_empty_payload_schema(self, tmp_path: Path) -> None:
        def no_properties(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["payload_schema"] = {"type": "object", "properties": {}}

        message = self._load_broken(tmp_path, no_properties)
        assert "at least one property" in message

    def test_a_duplicate_key_within_a_course(self, tmp_path: Path) -> None:
        def duplicate(doc: dict[str, Any]) -> None:
            doc["activity_types"].append(copy.deepcopy(doc["activity_types"][0]))

        message = self._load_broken(tmp_path, duplicate)
        assert "unit-one" in message
        assert "twice" in message

    def test_a_min_filled_above_the_property_count(self, tmp_path: Path) -> None:
        """Otherwise the shipped example is an activity nobody can pass."""

        def unreachable(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_config"]["min_filled"] = 3

        message = self._load_broken(tmp_path, unreachable)
        assert "min_filled" in message

    def test_a_negative_min_filled(self, tmp_path: Path) -> None:
        def negative(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_config"]["min_filled"] = -1

        message = self._load_broken(tmp_path, negative)
        assert "min_filled" in message

    def test_an_unregistered_validator_id(self, tmp_path: Path) -> None:
        """Since the move, the config is checked against the context's registry.

        An unknown validator is an error rather than a skip: accepting it would
        defer the same refusal to install time, or -- worse -- to a per-submission
        error verdict in front of a class.
        """

        def unknown(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_config"] = {"validator_id": "no-such-validator"}

        message = self._load_broken(tmp_path, unknown)
        assert "no-such-validator" in message
        assert "registered" in message

    def test_a_missing_validator_id(self, tmp_path: Path) -> None:
        def blank(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_config"] = {}

        message = self._load_broken(tmp_path, blank)
        assert "validator_id" in message

    def test_an_exact_match_config_is_checked_by_its_own_validator(self, tmp_path: Path) -> None:
        """Not just filled_count: going through the registry means every
        registered validator's config rules reach a course file, where before only
        one validator's did."""

        def exact_match_without_expected(doc: dict[str, Any]) -> None:
            doc["activity_types"][0]["validator_config"] = {
                "validator_id": "exact_match",
                "field": "one",
            }

        message = self._load_broken(tmp_path, exact_match_without_expected)
        assert "expected" in message

    def test_a_course_key_that_disagrees_with_the_filename(self, tmp_path: Path) -> None:
        def renamed(doc: dict[str, Any]) -> None:
            doc["course_key"] = "some-other-course"

        message = self._load_broken(tmp_path, renamed)
        assert "course_key" in message

    def test_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "fixture-course.json").write_bytes(b'{"course_key": ')
        with pytest.raises(CourseFileInvalid, match=r"fixture-course\.json"):
            load_course("fixture-course", root=tmp_path)

    def test_a_file_that_is_not_utf8(self, tmp_path: Path) -> None:
        """A Chinese course saved as Big5 is a realistic mistake for the person
        this file format exists to serve, so it must not surface as a traceback."""
        document = json.dumps(_course_document(), ensure_ascii=False)
        (tmp_path / "fixture-course.json").write_bytes(document.encode("big5"))

        with pytest.raises(CourseFileInvalid, match="not UTF-8"):
            load_course("fixture-course", root=tmp_path)

    def test_a_utf8_file_with_a_byte_order_mark(self, tmp_path: Path) -> None:
        """Windows editors commonly add a BOM; it must not read as broken JSON."""
        document = json.dumps(_course_document(), ensure_ascii=False)
        (tmp_path / "fixture-course.json").write_bytes(b"\xef\xbb\xbf" + document.encode("utf-8"))

        course = load_course("fixture-course", root=tmp_path)

        assert course.activity_types[0].name == "單元一 測試"

    def test_an_absent_catalogue_directory(self, tmp_path: Path) -> None:
        """The shape of the packaging failure: a wheel built without the course
        files. It has to read as an empty catalogue, not a FileNotFoundError."""
        missing = tmp_path / "no-such-directory"

        assert available_courses(root=missing) == ()
        with pytest.raises(CourseFileInvalid, match="available: none"):
            load_course("fixture-course", root=missing)

    def test_an_absent_course_names_what_is_available(self, tmp_path: Path) -> None:
        _write_course(tmp_path, _course_document())
        with pytest.raises(CourseFileInvalid) as excinfo:
            load_course("missing-course", root=tmp_path)
        assert "fixture-course" in str(excinfo.value)

    @pytest.mark.parametrize(
        "course_key",
        [
            "../secrets",
            "..",
            "a/b",
            "a\\b",
            "Course",
            "with_underscore",
            "",
            "creative-thinking.json",
            # `$` would accept this; the guard is anchored with \Z so it does not.
            "creative-thinking\n",
            "nul\x00byte",
        ],
    )
    def test_a_key_that_could_escape_the_catalogue_directory(self, tmp_path: Path, course_key: str) -> None:
        with pytest.raises(CourseFileInvalid, match="not a valid course key"):
            load_course(course_key, root=tmp_path)
