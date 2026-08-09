"""Course catalogue: the shape of a course and how one is read off disk.

A pure parser. It must not import :mod:`._seeding` or any context facade — that
is what keeps it cheap to unit-test over every shipped course file without a
database, and what stops course content from acquiring persistence concerns.

Course files are JSON under ``courses/`` so that the people who author the
content — educators, not engineers — can edit a prompt without touching Python
or the toolchain. The cost is that correctness moves from static types to
runtime validation, which is what everything below exists to pay for. Every
field is required, including the three visibility/retention flags: omitting
``expose_payload_to_agent`` would silently default a course to sending
participant text to an LLM provider, and a default is the wrong way to make that
decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from app.plugins.activity_validators import FILLED_COUNT_ID, validate_filled_count_config
from contexts.activities.application.validators.schema import validate_schema_wellformed
from contexts.activities.domain.errors import PayloadSchemaInvalid, ValidatorConfigInvalid
from contexts.activities.domain.models import ValidatorKind

COURSES_DIRNAME = "courses"

# A course key is also a filename component, so it may not contain a separator
# or a dot: `load_course("../../etc/passwd")` must not resolve.
_COURSE_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_COURSE_FIELDS = frozenset({"course_key", "title", "source", "activity_types"})
_TYPE_FIELDS = frozenset(
    {
        "key",
        "name",
        "payload_schema",
        "validator_config",
        "validator_kind",
        "retention_days",
        "expose_payload_to_agent",
        "echo_includes_content",
    }
)


class CourseFileInvalid(ValueError):
    """A course file does not describe a usable course.

    Raised with the file name and the offending key, because the operator who
    sees this is editing JSON by hand and needs to know where to look.
    """


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


def _fail(source: str, where: str, problem: str) -> CourseFileInvalid:
    return CourseFileInvalid(f"{source}: {where}: {problem}")


def _require_fields(source: str, where: str, data: dict[str, Any], allowed: frozenset[str]) -> None:
    missing = sorted(allowed - data.keys())
    if missing:
        raise _fail(source, where, f"missing required field(s) {', '.join(missing)}")
    unknown = sorted(data.keys() - allowed)
    if unknown:
        raise _fail(source, where, f"unknown field(s) {', '.join(unknown)}")


def _require_str(source: str, where: str, data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{where}.{key}", "must be a non-empty string")
    return value


def _require_bool(source: str, where: str, data: dict[str, Any], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise _fail(source, f"{where}.{key}", "must be true or false")
    return value


def _parse_payload_schema(source: str, where: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(source, f"{where}.payload_schema", "must be an object")
    try:
        validate_schema_wellformed(value)
    except PayloadSchemaInvalid as exc:
        raise _fail(source, f"{where}.payload_schema", f"is not a valid JSON Schema: {exc}") from exc
    properties = value.get("properties")
    # Mirrors the authoring form's `schemaEmpty` rule: a type with no declared
    # properties collects nothing, and `filled_count` would score every
    # submission against a field count of zero.
    if not isinstance(properties, dict) or not properties:
        raise _fail(source, f"{where}.payload_schema", "must declare at least one property")
    return value


def _parse_validator_config(source: str, where: str, value: Any, *, property_count: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(source, f"{where}.validator_config", "must be an object")
    if value.get("validator_id") != FILLED_COUNT_ID:
        return value
    try:
        validate_filled_count_config(value)
    except ValidatorConfigInvalid as exc:
        raise _fail(source, f"{where}.validator_config", str(exc)) from exc
    min_filled = int(value["min_filled"])
    if min_filled > property_count:
        # The scorer counts declared properties only, so a threshold above the
        # field count can never be met: a shipped example would be an activity
        # nobody can pass. The backend's config validator cannot catch this — it
        # sees the config without the schema — so it is checked here, the same
        # way the authoring form checks it.
        raise _fail(
            source,
            f"{where}.validator_config.min_filled",
            f"is {min_filled}, above the {property_count} declared propert"
            f"{'y' if property_count == 1 else 'ies'}",
        )
    return value


def _parse_retention_days(source: str, where: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(source, f"{where}.retention_days", "must be null or a positive integer")
    return int(value)


def _parse_activity_type(source: str, index: int, data: Any) -> CourseActivityType:
    where = f"activity_types[{index}]"
    if not isinstance(data, dict):
        raise _fail(source, where, "must be an object")
    _require_fields(source, where, data, _TYPE_FIELDS)

    key = _require_str(source, where, data, "key")
    where = f"activity_types[{index}] '{key}'"

    kind = data["validator_kind"]
    try:
        validator_kind = ValidatorKind(kind)
    except ValueError as exc:
        allowed = ", ".join(k.value for k in ValidatorKind)
        raise _fail(source, f"{where}.validator_kind", f"{kind!r} is not one of {allowed}") from exc

    payload_schema = _parse_payload_schema(source, where, data["payload_schema"])
    return CourseActivityType(
        key=key,
        name=_require_str(source, where, data, "name"),
        payload_schema=payload_schema,
        validator_config=_parse_validator_config(
            source,
            where,
            data["validator_config"],
            property_count=len(payload_schema["properties"]),
        ),
        validator_kind=validator_kind,
        retention_days=_parse_retention_days(source, where, data["retention_days"]),
        expose_payload_to_agent=_require_bool(source, where, data, "expose_payload_to_agent"),
        echo_includes_content=_require_bool(source, where, data, "echo_includes_content"),
    )


def parse_course(data: Any, *, source: str) -> CourseDefinition:
    """Validate one decoded course document. ``source`` names it in every error."""
    if not isinstance(data, dict):
        raise _fail(source, "course", "must be a JSON object")
    _require_fields(source, "course", data, _COURSE_FIELDS)

    raw_types = data["activity_types"]
    if not isinstance(raw_types, list) or not raw_types:
        raise _fail(source, "activity_types", "must be a non-empty array")

    activity_types = tuple(_parse_activity_type(source, i, t) for i, t in enumerate(raw_types))

    seen: set[str] = set()
    for activity_type in activity_types:
        if activity_type.key in seen:
            raise _fail(source, f"activity_types '{activity_type.key}'", "is declared twice")
        seen.add(activity_type.key)

    return CourseDefinition(
        course_key=_require_str(source, "course", data, "course_key"),
        title=_require_str(source, "course", data, "title"),
        source=_require_str(source, "course", data, "source"),
        activity_types=activity_types,
    )


def courses_root() -> Traversable:
    """The shipped catalogue directory, resolved as package data."""
    return files(__package__).joinpath(COURSES_DIRNAME)


def available_courses(*, root: Traversable | Path | None = None) -> tuple[str, ...]:
    """Course keys the catalogue ships, sorted."""
    base = root if root is not None else courses_root()
    return tuple(sorted(e.name[: -len(".json")] for e in base.iterdir() if e.name.endswith(".json")))


def load_course(course_key: str, *, root: Traversable | Path | None = None) -> CourseDefinition:
    """Read and validate one course file.

    ``root`` overrides the shipped catalogue directory; a course dropped into any
    directory loads the same way, which is what makes a new course a data file
    rather than a code change.
    """
    if not _COURSE_KEY_RE.match(course_key):
        raise CourseFileInvalid(
            f"{course_key!r} is not a valid course key (lowercase words joined by hyphens)"
        )

    base = root if root is not None else courses_root()
    filename = f"{course_key}.json"
    course_file = base.joinpath(filename)
    if not course_file.is_file():
        known = ", ".join(available_courses(root=root)) or "none"
        raise CourseFileInvalid(f"{filename}: no such course in the catalogue (available: {known})")

    # Explicit UTF-8: course text is Chinese, and the platform default would
    # mojibake it on a Windows host.
    try:
        data = json.loads(course_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _fail(filename, f"line {exc.lineno}", f"is not valid JSON: {exc.msg}") from exc

    course = parse_course(data, source=filename)
    if course.course_key != course_key:
        raise _fail(
            filename, "course.course_key", f"is {course.course_key!r} but the file is named {filename!r}"
        )
    return course


__all__ = [
    "CourseActivityType",
    "CourseDefinition",
    "CourseFileInvalid",
    "available_courses",
    "courses_root",
    "load_course",
    "parse_course",
]
