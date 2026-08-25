"""Shipped examples: read the catalogue, install a course, opt a project in/out.

Three audiences, one service, because they share one subject — the platform-scoped
rows a shipped course becomes:

- a platform admin reads the catalogue and installs a course ([R30.32]);
- a Project Owner sees what is installed and enables one for their project;
- opting out revokes that project's authorization and ends *its* activations only
  ([R30.33]).

Nothing here is automatic. Installation is a deliberate, audited act with a real
actor id — the operative half of [R30.28] is that example content is never
registered at startup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.activation_service import ActivationService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.domain.errors import (
    ActivityTypeNotFound,
    ActivityTypeNotOptedIn,
    ExampleCourseNotFound,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import ActivityType, ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure.examples.catalogue import (
    CourseDefinition,
    CourseFileInvalid,
    available_courses,
    load_course,
)
from contexts.activities.infrastructure.repositories.activation_repo import ActivationRepository
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel import audit

# The shipped courses cannot change without a redeploy, so parsing them once per
# process is safe and the admin listing stops re-reading and re-validating every
# file on every request. Only successful parses are cached: a failure that
# depended on process state (an unpopulated validator registry) must be
# retryable, not frozen in for the lifetime of the worker.
_PARSED: dict[str, CourseDefinition] = {}


def _load_cached(course_key: str) -> CourseDefinition:
    cached = _PARSED.get(course_key)
    if cached is None:
        cached = load_course(course_key)
        _PARSED[course_key] = cached
    return cached


def clear_catalogue_cache() -> None:
    """Test hook — drop the process-global parsed-course cache."""
    _PARSED.clear()


@dataclass(frozen=True, slots=True)
class CatalogueTypeEntry:
    """One activity type a course would install, and whether it already is."""

    key: str
    name: str
    expose_payload_to_agent: bool
    echo_includes_content: bool
    retention_days: int | None
    installed_type_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    course_key: str
    title: str
    source: str
    activity_types: tuple[CatalogueTypeEntry, ...]

    @property
    def fully_installed(self) -> bool:
        return all(t.installed_type_id is not None for t in self.activity_types)


@dataclass(frozen=True, slots=True)
class InstallReport:
    """What one install changed, mirroring the CLI seeder's report shape."""

    course_key: str
    created: tuple[str, ...]
    already_present: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlatformExample:
    """An installed platform type as one project sees it."""

    activity_type: ActivityType
    enabled: bool


class ActivityExampleService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._types = ActivityTypeService(db)
        self._type_repo = ActivityTypeRepository(db)
        self._optin_repo = ProjectActivityTypeOptInRepository(db)
        self._session_repo = ActivitySessionRepository(db)

    # -- Catalogue (admin) --------------------------------------------------

    async def list_catalogue(self) -> tuple[CatalogueEntry, ...]:
        """Every shipped course, annotated with what is already installed.

        A course file that fails to parse is skipped rather than failing the whole
        listing: one malformed file must not hide the courses an admin could still
        install. It is logged rather than swallowed — the course would otherwise be
        simply absent from the catalogue, which looks identical to a course that
        was never shipped, and an operator has nothing to go on.
        """
        courses: list[CourseDefinition] = []
        for course_key in available_courses():
            try:
                courses.append(_load_cached(course_key))
            except CourseFileInvalid:
                logger.warning("shipped course {} is not loadable; omitted from the catalogue", course_key)
                continue

        wanted = {t.key for course in courses for t in course.activity_types}
        installed = {t.key: t.id for t in await self._type_repo.list_platform_by_keys(sorted(wanted))}

        return tuple(
            CatalogueEntry(
                course_key=course.course_key,
                title=course.title,
                source=course.source,
                activity_types=tuple(
                    CatalogueTypeEntry(
                        key=t.key,
                        name=t.name,
                        expose_payload_to_agent=t.expose_payload_to_agent,
                        echo_includes_content=t.echo_includes_content,
                        retention_days=t.retention_days,
                        installed_type_id=installed.get(t.key),
                    )
                    for t in course.activity_types
                ),
            )
            for course in courses
        )

    async def install_course(
        self,
        *,
        course_key: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> InstallReport:
        """Install a course's types as platform-scoped rows ([R30.32]).

        Idempotent by key, mirroring the CLI seeder: a type that already exists is
        reported as already-present rather than conflicting, so a re-run after a
        partial failure is safe and a double-click is a no-op.

        ``course_key`` reaches here from an HTTP path parameter, so the loader's
        traversal guard now bounds a network-reachable path rather than a CLI
        argument. ``load_course`` rejects anything that is not lowercase words
        joined by hyphens before it touches the filesystem.

        The catalogue is consulted *before* the loader so an unknown key is a
        mapped 404 rather than the loader's ``CourseFileInvalid`` -- a bare
        ``ValueError`` outside this context's error contract, which reaches the
        client as an unhandled 500 with a logged stack trace. A key that names a
        shipped file which does not parse deliberately keeps producing that 500:
        that is a defect in the deployed artifact, not a client mistake.
        """
        known = available_courses()
        if course_key not in known:
            # The available list rather than the bare key: this route is
            # admin-gated, and in the realistic packaging-failure case ("none")
            # the list *is* the whole diagnosis.
            raise ExampleCourseNotFound(
                f"{course_key!r} is not a shipped course (available: {', '.join(known) or 'none'})"
            )
        course = _load_cached(course_key)

        for course_type in course.activity_types:
            if course_type.validator_kind is not ValidatorKind.IN_PROCESS:
                # An mcp validator names an agent/binding inside one project
                # ([R30.24]) and a webhook's egress carries one project's allowlist
                # and rate limit ([R30.07]). A platform row has no project to
                # supply either, so such a type could be installed but never
                # scored. Refuse the install rather than ship a dead type.
                raise ValidatorConfigInvalid(
                    f"course {course_key!r} type {course_type.key!r} declares a "
                    f"{course_type.validator_kind.value} validator; a platform-scoped type "
                    "must use an in_process validator"
                )

        already = await self._type_repo.list_platform_by_keys([t.key for t in course.activity_types])
        existing = {t.key for t in already}

        created: list[str] = []
        already_present: list[str] = []
        for course_type in course.activity_types:
            if course_type.key in existing:
                already_present.append(course_type.key)
                continue
            # register() emits activity_type.created with the admin as actor, so
            # the install needs no audit event of its own per type.
            await self._types.register(
                project_id=None,
                scope=ActivityTypeScope.PLATFORM,
                key=course_type.key,
                name=course_type.name,
                payload_schema=course_type.payload_schema,
                validator_kind=course_type.validator_kind,
                validator_config=course_type.validator_config,
                retention_days=course_type.retention_days,
                expose_payload_to_agent=course_type.expose_payload_to_agent,
                echo_includes_content=course_type.echo_includes_content,
                group_config=course_type.group_config,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            created.append(course_type.key)

        return InstallReport(
            course_key=course.course_key,
            created=tuple(created),
            already_present=tuple(already_present),
        )

    # -- Opt-in (project owner) ---------------------------------------------

    async def list_for_project(self, project_id: uuid.UUID) -> tuple[PlatformExample, ...]:
        """Installed platform types plus this project's enabled state.

        Owner-visible by design (OQ-2 of the dossier): the catalogue of installed
        examples is platform metadata, not another tenant's data, and an owner has
        to see it to enable one. Two queries, not one per row.
        """
        types = await self._type_repo.list_platform()
        enabled = {o.activity_type_id for o in await self._optin_repo.list_for_project(project_id)}
        return tuple(PlatformExample(activity_type=t, enabled=t.id in enabled) for t in types)

    async def opt_in(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Authorize one project to use one platform type ([R30.33]).

        Refuses anything that is not a live platform type — an opt-in row naming a
        project-scoped id would be meaningless (the reachability predicate ignores
        ``opted_in`` for those) and would pollute the admin-delete blast radius.

        Returns whether the project already owns a live type under this key, which
        the caller surfaces as a warning ([R30.02]). This is the mirror of
        ``type_service.register``'s check and exists because it is the *cheaper*
        door: authoring a colliding type takes a whole form, opting into one takes
        a click. It warns rather than refuses, for the same reason registration
        does. Reported as state rather than as this call's effect, so a repeat
        opt-in that changes nothing still reports the collision it left behind.
        """
        activity_type = await self._type_repo.get(activity_type_id)
        if activity_type is None or activity_type.scope is not ActivityTypeScope.PLATFORM:
            raise ActivityTypeNotFound(str(activity_type_id))

        owned = await self._type_repo.list_owned_by_project(project_id)
        shadows_owned_key = any(t.key == activity_type.key for t in owned)

        added = await self._optin_repo.add(
            project_id=project_id,
            activity_type_id=activity_type_id,
            enabled_by_user_id=actor_user_id,
        )
        if not added:
            return shadows_owned_key  # already enabled; nothing changed, so nothing to audit

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.opted_in",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=activity_type_id,
                metadata={"project_id": str(project_id), "key": activity_type.key},
                request_id=request_id,
            ),
        )
        return shadows_owned_key

    async def opt_out(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Revoke one project's access, ending only that project's activations.

        Deliberately not a code path shared with ``delete_platform_type``: that one
        ends every tenant's activations because the type is going away for
        everyone, and one project revoking its own access must not stop another
        project's class ([R30.33], AC-10).

        The bound comes from the project's own room ids, resolved through the
        conversation facade rather than a join ([R30.09]). Returns the
        ``(chatroom_id, activation_id)`` pairs ended so the route can broadcast
        ``activity.activation.ended`` post-commit.
        """
        activity_type = await self._type_repo.get(activity_type_id)
        if activity_type is None or activity_type.scope is not ActivityTypeScope.PLATFORM:
            raise ActivityTypeNotFound(str(activity_type_id))

        removed = await self._optin_repo.remove(project_id=project_id, activity_type_id=activity_type_id)
        if not removed:
            raise ActivityTypeNotOptedIn(str(activity_type_id))

        # The project's live rooms, resolved through the conversation facade rather
        # than a join ([R30.09]). This is the bound that keeps the cascade below
        # from reaching another tenant.
        room_ids = set(await ConversationFacade(self._db).list_chatroom_ids_for_project(project_id))
        activation_repo = ActivationRepository(self._db)
        activations = ActivationService(self._db, activation_repo=activation_repo, type_repo=self._type_repo)

        ended: list[tuple[uuid.UUID, uuid.UUID]] = []
        for activation in await activation_repo.list_active_for_type(activity_type_id):
            if activation.chatroom_id not in room_ids:
                continue
            result = await activations.end(
                chatroom_id=activation.chatroom_id,
                activation_id=activation.id,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            if result.transitioned:
                ended.append((activation.chatroom_id, activation.id))

        await self._session_repo.close_open_for_type_in_rooms(activity_type_id, sorted(room_ids))

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_type.opted_out",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_type",
                resource_id=activity_type_id,
                metadata={
                    "project_id": str(project_id),
                    "key": activity_type.key,
                    "activations_ended": str(len(ended)),
                },
                request_id=request_id,
            ),
        )
        return ended


__all__ = [
    "ActivityExampleService",
    "CatalogueEntry",
    "CatalogueTypeEntry",
    "InstallReport",
    "PlatformExample",
    "clear_catalogue_cache",
]
