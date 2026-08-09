"""Course-agnostic seeding engine: register a course's activity types once.

Holds no course content. Everything here is mechanism — idempotency, the session
and transaction boundary, and the report the CLI prints.

Idempotent: a type whose key already exists in the project is skipped, so a
re-run after a partial failure is safe.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.interfaces.facade import ActivitiesFacade
from shared_kernel.db.session import get_sessionmaker

from ._catalogue import CourseActivityType


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)


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
        existing = {t.key for t in await facade.list_types(project_id)}
        for course_type in activity_types:
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
