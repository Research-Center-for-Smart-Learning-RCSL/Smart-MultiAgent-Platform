"""Course-agnostic seeding engine: register a course's activity types once.

Holds no course content. Everything here is mechanism — idempotency, the session
and transaction boundary, and the report the CLI prints.

Idempotent: a type whose key the project already **owns** is skipped, so a
re-run after a partial failure is safe. Ownership is the test, not usability —
a platform-scoped type the project opted into shares the catalogue's keys but is
read-only to a Project Owner, so it is not the copy this seeder exists to make.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.domain.models import ActivityTypeScope
from contexts.activities.interfaces.facade import ActivitiesFacade
from shared_kernel.db.session import get_sessionmaker

from ._catalogue import CourseActivityType


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    # Keys this run created that now coexist with an opted-in platform type of the
    # same key. Creating the copy is what the operator asked for, but the plugin
    # registry and any workflow reactive rule match on the key alone and cannot
    # tell the two apart, so the operator has to be told.
    shadowed_by_platform: list[str] = field(default_factory=list)


async def seed_course(
    *,
    project_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    activity_types: Sequence[CourseActivityType],
) -> SeedReport:
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
        # Ownership, not usability: `list_types` is the union of what the project
        # owns and the platform types it opted into, and a platform row is
        # read-only to a Project Owner — so counting one as "already present"
        # would skip creating the editable copy this command exists to produce.
        owned = {t.key for t in await facade.list_owned_types(project_id)}
        # The usable set is still needed, but only to warn: a key created here that
        # also names an opted-in platform type leaves the project holding two types
        # under one key.
        shadowing = {
            t.key for t in await facade.list_types(project_id) if t.scope is ActivityTypeScope.PLATFORM
        }
        for course_type in activity_types:
            if course_type.key in owned:
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
            if course_type.key in shadowing:
                report.shadowed_by_platform.append(course_type.key)
        # The facade never commits; the caller owns the transaction boundary.
        await session.commit()
    return report


def run(
    *,
    project_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    activity_types: Sequence[CourseActivityType],
) -> SeedReport:
    """Synchronous entry point for the CLI command."""
    return asyncio.run(
        seed_course(
            project_id=project_id,
            owner_user_id=owner_user_id,
            activity_types=activity_types,
        )
    )


__all__ = ["SeedReport", "run", "seed_course"]
