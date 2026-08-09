"""Content of the two-unit creative-thinking course example ([R30.28]).

Source: Ke Pei-jung (2019), "Effect of Creative Thinking Skills Integrated into
Guidance Activity Curriculum Self-Development Theme Axis on Creativity and
Self-Concept for the Junior High School Students", MA thesis, National Taiwan
Normal University (advisor: Chen Hsueh-chih). Two of its eight units are modelled
here; see docs/examples/creative-thinking-course.md for the full mapping and the
limits of what this demonstrates.

Transitional: these constants are being replaced by courses/creative-thinking.json.
Seeding mechanics live in _seeding.py.
"""

from __future__ import annotations

import uuid
from typing import Any

from ._catalogue import CourseActivityType
from ._seeding import SeedReport
from ._seeding import run as _run


def _mandala_schema() -> dict[str, Any]:
    """Unit 2 — a radial mandala: one centre prompt plus eight free cells.

    The eight cells are deliberately unlabelled. The source figures (放射型曼陀羅)
    are free-association layouts; pre-theming the cells would constrain the
    divergent thinking the unit exists to elicit.
    """
    properties: dict[str, Any] = {
        "center": {
            "type": "string",
            "title": "中心主題：30 歲的我",
            "description": "用一句話寫下你想像中 30 歲的自己。",
        }
    }
    for i in range(1, 9):
        properties[f"cell_{i}"] = {"type": "string", "title": f"格 {i}"}
    return {"type": "object", "properties": properties, "required": ["center"]}


def _six_hats_schema() -> dict[str, Any]:
    """Unit 4 — one troubling event reviewed through five of de Bono's hats.

    Property order drives render order in the generic form. The source lists the
    five hats without fixing a sequence, so this uses de Bono's standard review
    order (facts, feelings, upside, risk, summary).
    """
    return {
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
    }


# `mandala-9grid` is the key the bundled frontend plugin binds to, so this unit
# renders as a 3x3 worksheet; the six-hats unit has no plugin and falls to the
# generic schema form, which is the point of shipping both ([R30.17], [R30.18]).
COURSE_TYPES: tuple[CourseActivityType, ...] = (
    CourseActivityType(
        key="mandala-9grid",
        name="單元二 時空旅人",
        payload_schema=_mandala_schema(),
        validator_config={"validator_id": "filled_count", "min_filled": 4},
    ),
    CourseActivityType(
        key="six-hats-emotion-desk",
        name="單元四 情緒播報台",
        payload_schema=_six_hats_schema(),
        validator_config={"validator_id": "filled_count", "min_filled": 3},
    ),
)


def run(project_id: uuid.UUID, owner_user_id: uuid.UUID) -> SeedReport:
    return _run(project_id=project_id, owner_user_id=owner_user_id, activity_types=COURSE_TYPES)


__all__ = ["COURSE_TYPES", "CourseActivityType", "SeedReport", "run"]
