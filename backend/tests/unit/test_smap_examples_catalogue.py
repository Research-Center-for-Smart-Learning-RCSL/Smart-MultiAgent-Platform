"""The course catalogue: the shipped content, pinned field-for-field, plus the
loader's rejection rules.

The expected values below were written *before* the Python-constants -> JSON
transcription, precisely so they could catch the one defect that transcription
is most likely to introduce: a prompt string altered in transit. They are
spelled out literally rather than derived from a loop, so they stay independent
of however the production side happens to build the schema. Edit them only when
the course content is deliberately changing.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from contexts.activities.domain.models import ValidatorKind
from smap.examples._catalogue import (
    CourseFileInvalid,
    available_courses,
    load_course,
)

MANDALA: dict[str, Any] = {
    "key": "mandala-9grid",
    "name": "單元二 時空旅人",
    "validator_kind": "in_process",
    "validator_config": {"validator_id": "filled_count", "min_filled": 4},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "center": {
                "type": "string",
                "title": "中心主題：30 歲的我",
                "description": "用一句話寫下你想像中 30 歲的自己。",
            },
            "cell_1": {"type": "string", "title": "格 1"},
            "cell_2": {"type": "string", "title": "格 2"},
            "cell_3": {"type": "string", "title": "格 3"},
            "cell_4": {"type": "string", "title": "格 4"},
            "cell_5": {"type": "string", "title": "格 5"},
            "cell_6": {"type": "string", "title": "格 6"},
            "cell_7": {"type": "string", "title": "格 7"},
            "cell_8": {"type": "string", "title": "格 8"},
        },
        "required": ["center"],
    },
}

SIX_HATS: dict[str, Any] = {
    "key": "six-hats-emotion-desk",
    "name": "單元四 情緒播報台",
    "validator_kind": "in_process",
    "validator_config": {"validator_id": "filled_count", "min_filled": 3},
    "retention_days": None,
    "expose_payload_to_agent": True,
    "echo_includes_content": False,
    "payload_schema": {
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "title": "困擾我的事件",
                "description": "最近或曾經讓自己困擾的一件事。",
            },
            "hat_white": {
                "type": "string",
                "title": "白帽：事實",
                "description": "只寫客觀發生了什麼，不加評價。",
            },
            "hat_red": {
                "type": "string",
                "title": "紅帽：感受",
                "description": "當下的情緒，不需要說明理由。",
            },
            "hat_yellow": {
                "type": "string",
                "title": "黃帽：好處",
                "description": "這件事有沒有任何好的一面？",
            },
            "hat_black": {
                "type": "string",
                "title": "黑帽：風險",
                "description": "可能的壞處或風險是什麼？",
            },
            "hat_blue": {
                "type": "string",
                "title": "藍帽：總結",
                "description": "整理以上，你現在的想法是什麼？",
            },
        },
        "required": ["event"],
    },
}

CREATIVE_THINKING_TYPES: tuple[dict[str, Any], ...] = (MANDALA, SIX_HATS)

SHIPPED_TYPES = load_course("creative-thinking").activity_types


def as_dicts(activity_types: Any) -> list[dict[str, Any]]:
    """Course types as plain dicts, so the pin does not depend on their class."""
    return [dataclasses.asdict(t) for t in activity_types]


class TestShippedCourseContent:
    """G-1: the exact seeded values, not merely their shape.

    Without this, a prompt string could be silently altered during the move and
    every other test in the suite would still pass.
    """

    def test_the_course_is_exactly_the_two_pinned_units(self) -> None:
        assert as_dicts(SHIPPED_TYPES) == list(CREATIVE_THINKING_TYPES)

    @pytest.mark.parametrize(
        ("index", "expected"),
        enumerate(CREATIVE_THINKING_TYPES),
        ids=[t["key"] for t in CREATIVE_THINKING_TYPES],
    )
    def test_each_unit_matches_field_for_field(self, index: int, expected: dict[str, Any]) -> None:
        """Same assertion split per unit, so a failure names the unit that drifted."""
        assert dataclasses.asdict(SHIPPED_TYPES[index]) == expected

    def test_property_order_is_preserved(self) -> None:
        """Property order drives render order in the generic form, so it is behavior.

        JSON object order is preserved by `json.loads`, but nothing in the format
        promises it, so a future loader change that normalised key order would
        silently reshuffle a worksheet.
        """
        for actual, expected in zip(SHIPPED_TYPES, CREATIVE_THINKING_TYPES, strict=True):
            assert list(actual.payload_schema["properties"]) == list(expected["payload_schema"]["properties"])

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

    def test_a_course_key_that_disagrees_with_the_filename(self, tmp_path: Path) -> None:
        def renamed(doc: dict[str, Any]) -> None:
            doc["course_key"] = "some-other-course"

        message = self._load_broken(tmp_path, renamed)
        assert "course_key" in message

    def test_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "fixture-course.json").write_bytes(b'{"course_key": ')
        with pytest.raises(CourseFileInvalid, match="fixture-course.json"):
            load_course("fixture-course", root=tmp_path)

    def test_an_absent_course_names_what_is_available(self, tmp_path: Path) -> None:
        _write_course(tmp_path, _course_document())
        with pytest.raises(CourseFileInvalid) as excinfo:
            load_course("missing-course", root=tmp_path)
        assert "fixture-course" in str(excinfo.value)

    @pytest.mark.parametrize("course_key", ["../secrets", "a/b", "Course", "with_underscore", ""])
    def test_a_key_that_could_escape_the_catalogue_directory(self, tmp_path: Path, course_key: str) -> None:
        with pytest.raises(CourseFileInvalid, match="not a valid course key"):
            load_course(course_key, root=tmp_path)
