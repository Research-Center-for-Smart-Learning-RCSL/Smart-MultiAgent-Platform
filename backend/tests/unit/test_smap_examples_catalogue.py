"""The shipped course content, pinned field-for-field.

Written *before* the Python-constants -> JSON transcription so it can catch the
one defect that transcription is most likely to introduce: a prompt string
altered in transit. The expected values below are spelled out literally rather
than derived from a loop, so they stay independent of however the production
side happens to build the schema.

Keep this fixture in terms of plain dicts. It has to survive the course
definitions changing class (module constants -> loader output) without being
rewritten, which is the whole reason it exists.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from smap.examples import creative_thinking_course as seeder

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


def as_dicts(activity_types: Any) -> list[dict[str, Any]]:
    """Course types as plain dicts, so the pin does not depend on their class."""
    return [dataclasses.asdict(t) for t in activity_types]


class TestShippedCourseContent:
    """G-1: the exact seeded values, not merely their shape.

    Without this, a prompt string could be silently altered during the move and
    every other test in the suite would still pass.
    """

    def test_the_course_is_exactly_the_two_pinned_units(self) -> None:
        assert as_dicts(seeder.COURSE_TYPES) == list(CREATIVE_THINKING_TYPES)

    @pytest.mark.parametrize(
        ("index", "expected"),
        enumerate(CREATIVE_THINKING_TYPES),
        ids=[t["key"] for t in CREATIVE_THINKING_TYPES],
    )
    def test_each_unit_matches_field_for_field(self, index: int, expected: dict[str, Any]) -> None:
        """Same assertion split per unit, so a failure names the unit that drifted."""
        assert dataclasses.asdict(seeder.COURSE_TYPES[index]) == expected

    def test_property_order_is_preserved(self) -> None:
        """Property order drives render order in the generic form, so it is behavior."""
        for actual, expected in zip(seeder.COURSE_TYPES, CREATIVE_THINKING_TYPES, strict=True):
            assert list(actual.payload_schema["properties"]) == list(expected["payload_schema"]["properties"])
