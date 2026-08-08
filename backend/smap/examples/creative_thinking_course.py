"""Seed the two-unit creative-thinking course example ([R30.28]).

Source: Ke Pei-jung (2019), "Effect of Creative Thinking Skills Integrated into
Guidance Activity Curriculum Self-Development Theme Axis on Creativity and
Self-Concept for the Junior High School Students", MA thesis, National Taiwan
Normal University (advisor: Chen Hsueh-chih). Two of its eight units are modelled
here; see docs/examples/creative-thinking-course.md for the full mapping and the
limits of what this demonstrates.

Idempotent: a type whose key already exists in the project is skipped, so a re-run
after a partial failure is safe.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.domain.models import ValidatorKind
from contexts.activities.interfaces.facade import ActivitiesFacade
from shared_kernel.db.session import get_sessionmaker


@dataclass(frozen=True, slots=True)
class CourseActivityType:
    """One seedable activity type, mirroring the facade's registration inputs."""

    key: str
    name: str
    payload_schema: dict[str, Any]
    validator_config: dict[str, Any]
    validator_kind: ValidatorKind = ValidatorKind.IN_PROCESS
    # Retention is an IRB decision, not a platform recommendation: seeded
    # submissions follow the room's normal purge until a study sets a horizon.
    retention_days: int | None = None
    # Room agents read the digest so they can respond; the room transcript does
    # not echo answer content back to everyone.
    expose_payload_to_agent: bool = True
    echo_includes_content: bool = False


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


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)


async def _seed(project_id: uuid.UUID, owner_user_id: uuid.UUID) -> SeedReport:
    # The in-process validator registry is process-global and empty until a
    # registration site runs. The API populates it from a startup step
    # (app/bootstrap/startup.py::register_activity_validators_step); a CLI process
    # runs no startup steps, so without this every register_type call below would
    # be rejected by _validate_validator_config as an unknown validator_id.
    register_first_party_validators()

    report = SeedReport()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        facade = ActivitiesFacade(session)
        existing = {t.key for t in await facade.list_types(project_id)}
        for course_type in COURSE_TYPES:
            if course_type.key in existing:
                report.already_present.append(course_type.key)
                continue
            await facade.register_type(
                project_id=project_id,
                key=course_type.key,
                name=course_type.name,
                payload_schema=course_type.payload_schema,
                validator_kind=course_type.validator_kind,
                validator_config=course_type.validator_config,
                retention_days=course_type.retention_days,
                expose_payload_to_agent=course_type.expose_payload_to_agent,
                echo_includes_content=course_type.echo_includes_content,
                actor_user_id=owner_user_id,
                actor_ip=None,
            )
            report.created.append(course_type.key)
        # The facade never commits; the caller owns the transaction boundary.
        await session.commit()
    return report


def run(project_id: uuid.UUID, owner_user_id: uuid.UUID) -> SeedReport:
    return asyncio.run(_seed(project_id, owner_user_id))


__all__ = ["COURSE_TYPES", "CourseActivityType", "SeedReport", "run"]
