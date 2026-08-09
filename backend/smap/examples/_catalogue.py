"""Course catalogue: the shape of a course and how one is read off disk.

A pure parser. It must not import :mod:`._seeding` or any context facade — that
is what keeps it cheap to unit-test over every shipped course file without a
database, and what stops course content from acquiring persistence concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contexts.activities.domain.models import ValidatorKind


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


@dataclass(frozen=True, slots=True)
class CourseDefinition:
    """One worked example: its provenance plus the types it seeds."""

    course_key: str
    title: str
    source: str
    activity_types: tuple[CourseActivityType, ...]


__all__ = ["CourseActivityType", "CourseDefinition"]
