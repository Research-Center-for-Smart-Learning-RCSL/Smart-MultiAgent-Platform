"""AC-3/AC-10: installing a shipped course, and opting a project in and out.

Runs against the *real* shipped catalogue rather than a fixture course, because
half of what AC-3 asserts is that the installed rows carry the JSON's own field
values -- a fixture would only prove the service copies whatever it was handed.

Repositories are mocked; `ActivityTypeService` is real, so the
`activity_type.created` audit event AC-3 requires is genuinely emitted rather than
assumed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.application import example_service
from contexts.activities.application.example_service import ActivityExampleService
from contexts.activities.domain.errors import (
    ActivityTypeNotFound,
    ActivityTypeNotOptedIn,
    ExampleCourseNotFound,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityActivationEndResult,
    ActivityType,
    ActivityTypeScope,
    ProjectActivityTypeOptIn,
    ValidatorKind,
)
from contexts.activities.infrastructure.examples.catalogue import (
    CourseDefinition,
    CourseFileInvalid,
    load_course,
)

_NOW = dt.datetime(2026, 8, 9, tzinfo=dt.UTC)
_COURSE_KEY = "creative-thinking"


@pytest.fixture(autouse=True)
def _clean_process_state() -> Any:
    """Two process-global things this module depends on, reset per test.

    The in-process validator registry, because the catalogue checks a course's
    validator_config against it and no app startup populates it in a bare unit
    run — and because another module's teardown calls ``clear_registry()``, so
    registering once at import is not enough.

    The parsed-course cache, so a course this module monkeypatches cannot leak
    into the next test or out of this module entirely.
    """
    register_first_party_validators()
    example_service.clear_catalogue_cache()
    yield
    example_service.clear_catalogue_cache()


def _platform_type(key: str, **over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": None,
        "scope": ActivityTypeScope.PLATFORM,
        "key": key,
        "name": key,
        "payload_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 1},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
    }
    base.update(over)
    return ActivityType(**base)


def _service(
    *,
    platform_types: list[ActivityType] | None = None,
    optins: list[ProjectActivityTypeOptIn] | None = None,
    owned_types: list[ActivityType] | None = None,
) -> ActivityExampleService:
    svc = ActivityExampleService(MagicMock())
    installed = platform_types or []

    svc._type_repo = MagicMock()
    svc._type_repo.list_platform = AsyncMock(return_value=installed)
    svc._type_repo.list_owned_by_project = AsyncMock(return_value=owned_types or [])
    svc._type_repo.list_platform_by_keys = AsyncMock(
        side_effect=lambda keys: [t for t in installed if t.key in set(keys)]
    )
    svc._type_repo.get = AsyncMock(
        side_effect=lambda type_id: next((t for t in installed if t.id == type_id), None)
    )

    svc._optin_repo = MagicMock()
    svc._optin_repo.list_for_project = AsyncMock(return_value=optins or [])
    svc._optin_repo.add = AsyncMock(return_value=True)
    svc._optin_repo.remove = AsyncMock(return_value=True)

    svc._session_repo = MagicMock()
    svc._session_repo.close_open_for_type_in_rooms = AsyncMock(return_value=0)

    # Real type service, mocked repository: `register` still runs its validation
    # and emits the audit event, which is what AC-3 is about.
    created: dict[uuid.UUID, ActivityType] = {}

    async def _create(**kw: Any) -> uuid.UUID:
        new_id = uuid.uuid4()
        created[new_id] = ActivityType(
            id=new_id,
            project_id=kw["project_id"],
            scope=kw["scope"],
            key=kw["key"],
            name=kw["name"],
            payload_schema=kw["payload_schema"],
            validator_kind=kw["validator_kind"],
            validator_config=kw["validator_config"],
            retention_days=kw["retention_days"],
            expose_payload_to_agent=kw["expose_payload_to_agent"],
            echo_includes_content=kw["echo_includes_content"],
            version=1,
            created_at=_NOW,
        )
        return new_id

    svc._types._repo = MagicMock()
    svc._types._repo.create = AsyncMock(side_effect=_create)
    svc._types._repo.get = AsyncMock(side_effect=created.get)
    svc._types._policy._repo = MagicMock(get_platform=AsyncMock(return_value=None))
    svc._created_rows = created  # type: ignore[attr-defined]  # exposed for assertions
    return svc


class TestInstallCourse:
    async def test_it_installs_every_type_with_the_courses_own_values(self) -> None:
        """AC-3: the installed rows carry the JSON's field values, not defaults."""
        svc = _service()
        course = load_course(_COURSE_KEY)

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            report = await svc.install_course(
                course_key=_COURSE_KEY, actor_user_id=uuid.uuid4(), actor_ip=None
            )

        assert report.created == tuple(t.key for t in course.activity_types)
        assert report.already_present == ()

        rows = {r.key: r for r in svc._created_rows.values()}  # type: ignore[attr-defined]
        assert set(rows) == {t.key for t in course.activity_types}
        for course_type in course.activity_types:
            row = rows[course_type.key]
            assert row.scope is ActivityTypeScope.PLATFORM
            assert row.project_id is None
            assert row.name == course_type.name
            assert row.payload_schema == course_type.payload_schema
            assert row.validator_config == course_type.validator_config
            assert row.validator_kind is course_type.validator_kind
            assert row.retention_days == course_type.retention_days
            assert row.expose_payload_to_agent == course_type.expose_payload_to_agent
            assert row.echo_includes_content == course_type.echo_includes_content

    async def test_it_audits_each_created_type_with_the_admin_as_actor(self) -> None:
        """AC-3's other half: an install is an audited act with a real actor id."""
        svc = _service()
        admin_id = uuid.uuid4()
        course = load_course(_COURSE_KEY)

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            await svc.install_course(course_key=_COURSE_KEY, actor_user_id=admin_id, actor_ip="10.0.0.9")

        events = [call.args[1] for call in emit.await_args_list]
        assert len(events) == len(course.activity_types)
        assert {e.action for e in events} == {"activity_type.created"}
        assert {e.actor_user_id for e in events} == {admin_id}
        assert {e.metadata["scope"] for e in events} == {"platform"}
        assert {e.metadata["project_id"] for e in events} == {None}

    async def test_a_second_install_reports_already_present_and_creates_nothing(self) -> None:
        """AC-2: idempotent by key, so a re-run after a partial failure is safe."""
        course = load_course(_COURSE_KEY)
        svc = _service(platform_types=[_platform_type(t.key) for t in course.activity_types])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
            report = await svc.install_course(
                course_key=_COURSE_KEY, actor_user_id=uuid.uuid4(), actor_ip=None
            )

        assert report.created == ()
        assert report.already_present == tuple(t.key for t in course.activity_types)
        emit.assert_not_awaited()

    async def test_a_partial_install_creates_only_the_missing_type(self) -> None:
        course = load_course(_COURSE_KEY)
        first, *rest = (t.key for t in course.activity_types)
        svc = _service(platform_types=[_platform_type(first)])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            report = await svc.install_course(
                course_key=_COURSE_KEY, actor_user_id=uuid.uuid4(), actor_ip=None
            )

        assert report.already_present == (first,)
        assert report.created == tuple(rest)

    async def test_an_unknown_course_key_is_a_domain_not_found(self) -> None:
        """F-6. A mistyped key is the client's fault, so it must be the mapped 404
        rather than the loader's bare `CourseFileInvalid`, which falls through to
        the global catch-all as a 500 with a logged stack trace."""
        svc = _service()

        with pytest.raises(ExampleCourseNotFound) as exc:
            await svc.install_course(course_key="creative-thinkin", actor_user_id=uuid.uuid4(), actor_ip=None)

        # AC-4: the detail names what *is* available, which is the whole diagnosis
        # when a packaging failure leaves the catalogue empty.
        assert _COURSE_KEY in str(exc.value)

    async def test_a_traversal_course_key_never_reaches_the_filesystem(self) -> None:
        """`course_key` is an HTTP path segment now, so nothing derived from it may
        reach the filesystem. The catalogue pre-check refuses it before the loader
        runs at all, which is why this now raises the domain not-found; the guard
        being asserted is that no `open`/`is_file` ever sees the key."""
        svc = _service()

        def _never(_key: str) -> CourseDefinition:
            raise AssertionError("the loader was reached with a client-supplied key")

        with (
            patch.object(example_service, "_load_cached", _never),
            patch.object(Path, "is_file", side_effect=AssertionError("filesystem touched")),
            patch.object(Path, "read_text", side_effect=AssertionError("filesystem touched")),
            pytest.raises(ExampleCourseNotFound),
        ):
            await svc.install_course(course_key="../../etc/passwd", actor_user_id=uuid.uuid4(), actor_ip=None)

    async def test_a_malformed_shipped_file_is_still_a_server_fault(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-2. The not-found lift must not swallow artifact failures: a key that
        names a real shipped file which does not parse stays a `CourseFileInvalid`,
        so it stays a 500 an operator is paged about."""
        svc = _service()

        def _explode(_key: str) -> CourseDefinition:
            raise CourseFileInvalid(f"{_key}.json: line 3: is not valid JSON")

        monkeypatch.setattr(example_service, "_load_cached", _explode)

        with pytest.raises(CourseFileInvalid):
            await svc.install_course(course_key=_COURSE_KEY, actor_user_id=uuid.uuid4(), actor_ip=None)

    async def test_a_remote_validator_course_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A platform row has no project, so an mcp/webhook validator could never
        run for it ([R30.07], [R30.24]). Refuse the install rather than ship a type
        that installs cleanly and fails every submission."""
        svc = _service()
        course = load_course(_COURSE_KEY)
        import dataclasses

        remote = dataclasses.replace(
            course,
            activity_types=(
                dataclasses.replace(
                    course.activity_types[0],
                    validator_kind=ValidatorKind.WEBHOOK,
                    validator_config={"url": "https://example.test/score"},
                ),
            ),
        )
        monkeypatch.setattr(example_service, "_load_cached", lambda _key: remote)

        with pytest.raises(ValidatorConfigInvalid, match="in_process"):
            await svc.install_course(course_key=_COURSE_KEY, actor_user_id=uuid.uuid4(), actor_ip=None)


class TestCatalogueListing:
    async def test_it_reports_which_types_are_already_installed(self) -> None:
        course = load_course(_COURSE_KEY)
        first = course.activity_types[0]
        installed = _platform_type(first.key)
        svc = _service(platform_types=[installed])

        entries = {e.course_key: e for e in await svc.list_catalogue()}
        entry = entries[_COURSE_KEY]

        by_key = {t.key: t for t in entry.activity_types}
        assert by_key[first.key].installed_type_id == installed.id
        assert entry.fully_installed is (len(course.activity_types) == 1)
        assert all(t.installed_type_id is None for k, t in by_key.items() if k != first.key)

    async def test_it_surfaces_the_agent_exposure_flag_for_the_consent_prompt(self) -> None:
        """The import dialog has to state that participant text reaches the LLM
        provider, so the flag has to be on the listing rather than fetched per row."""
        svc = _service()
        course = load_course(_COURSE_KEY)

        entry = next(e for e in await svc.list_catalogue() if e.course_key == _COURSE_KEY)

        by_key = {t.key: t for t in entry.activity_types}
        for course_type in course.activity_types:
            assert by_key[course_type.key].expose_payload_to_agent == course_type.expose_payload_to_agent


class TestOptIn:
    async def test_it_records_the_optin_and_audits_it(self) -> None:
        installed = _platform_type("mandala-9grid")
        svc = _service(platform_types=[installed])
        project_id, actor = uuid.uuid4(), uuid.uuid4()

        with patch("contexts.activities.application.example_service.audit.emit", new=AsyncMock()) as emit:
            await svc.opt_in(
                project_id=project_id,
                activity_type_id=installed.id,
                actor_user_id=actor,
                actor_ip=None,
            )

        svc._optin_repo.add.assert_awaited_once_with(
            project_id=project_id, activity_type_id=installed.id, enabled_by_user_id=actor
        )
        event = emit.await_args.args[1]
        assert event.action == "activity_type.opted_in"
        assert event.metadata["project_id"] == str(project_id)

    async def test_a_repeat_optin_changes_nothing_and_audits_nothing(self) -> None:
        installed = _platform_type("mandala-9grid")
        svc = _service(platform_types=[installed])
        svc._optin_repo.add = AsyncMock(return_value=False)

        with patch("contexts.activities.application.example_service.audit.emit", new=AsyncMock()) as emit:
            await svc.opt_in(
                project_id=uuid.uuid4(),
                activity_type_id=installed.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        emit.assert_not_awaited()

    async def test_opting_into_a_key_the_project_already_owns_warns_and_succeeds(self) -> None:
        """AC-2. The mirror of `register`'s warning, and the cheaper of the two
        doors onto the same state: authoring the collision takes a whole form,
        opting into it takes one click. Permitted either way ([R30.02])."""
        installed = _platform_type("mandala-9grid")
        owned = _platform_type("mandala-9grid", project_id=uuid.uuid4(), scope=ActivityTypeScope.PROJECT)
        svc = _service(platform_types=[installed], owned_types=[owned])

        with patch("contexts.activities.application.example_service.audit.emit", new=AsyncMock()):
            shadows = await svc.opt_in(
                project_id=uuid.uuid4(),
                activity_type_id=installed.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert shadows is True
        svc._optin_repo.add.assert_awaited_once()

    async def test_no_warning_when_the_project_owns_no_type_under_that_key(self) -> None:
        installed = _platform_type("mandala-9grid")
        owned = _platform_type("quiz", project_id=uuid.uuid4(), scope=ActivityTypeScope.PROJECT)
        svc = _service(platform_types=[installed], owned_types=[owned])

        with patch("contexts.activities.application.example_service.audit.emit", new=AsyncMock()):
            shadows = await svc.opt_in(
                project_id=uuid.uuid4(),
                activity_type_id=installed.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert shadows is False

    async def test_a_repeat_optin_still_reports_a_standing_collision(self) -> None:
        """The warning is about state, not about this call's effect. A re-run that
        changes nothing must keep reporting the collision it left behind, or going
        silent would read as though the owner had resolved it."""
        installed = _platform_type("mandala-9grid")
        owned = _platform_type("mandala-9grid", project_id=uuid.uuid4(), scope=ActivityTypeScope.PROJECT)
        svc = _service(platform_types=[installed], owned_types=[owned])
        svc._optin_repo.add = AsyncMock(return_value=False)

        shadows = await svc.opt_in(
            project_id=uuid.uuid4(),
            activity_type_id=installed.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        assert shadows is True

    async def test_a_project_scoped_type_cannot_be_opted_into(self) -> None:
        """An opt-in row naming a project type would be meaningless -- the
        reachability predicate ignores `opted_in` for those -- and would pollute
        the admin-delete blast radius."""
        project_type = _platform_type("quiz", project_id=uuid.uuid4(), scope=ActivityTypeScope.PROJECT)
        svc = _service(platform_types=[project_type])

        with pytest.raises(ActivityTypeNotFound):
            await svc.opt_in(
                project_id=uuid.uuid4(),
                activity_type_id=project_type.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        svc._optin_repo.add.assert_not_awaited()


class TestOptOut:
    """AC-10: opting out ends only the opting-out project's activations."""

    def _with_rooms(
        self, svc: ActivityExampleService, *, mine: list[uuid.UUID], theirs: list[uuid.UUID]
    ) -> tuple[MagicMock, list[ActivityActivation]]:
        activations = [
            ActivityActivation(
                id=uuid.uuid4(),
                chatroom_id=room,
                activity_type_id=uuid.uuid4(),
                started_by_user_id=uuid.uuid4(),
                status=ActivationStatus.ACTIVE,
                created_at=_NOW,
            )
            for room in mine + theirs
        ]
        activation_repo = MagicMock(list_active_for_type=AsyncMock(return_value=activations))
        return activation_repo, activations

    async def test_it_ends_only_this_projects_activations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        installed = _platform_type("mandala-9grid")
        svc = _service(platform_types=[installed])
        my_room, their_room = uuid.uuid4(), uuid.uuid4()
        activation_repo, activations = self._with_rooms(svc, mine=[my_room], theirs=[their_room])

        ended_ids: list[uuid.UUID] = []

        async def _end(*, chatroom_id: uuid.UUID, activation_id: uuid.UUID, **_kw: Any) -> Any:
            ended_ids.append(activation_id)
            match = next(a for a in activations if a.id == activation_id)
            return ActivityActivationEndResult(activation=match, transitioned=True)

        monkeypatch.setattr(
            example_service,
            "ConversationFacade",
            lambda _db: MagicMock(list_chatroom_ids_for_project=AsyncMock(return_value=[my_room])),
        )
        # Patched where they are *used*, not where they are defined: the service
        # binds both names at import, so patching the defining module would leave
        # the already-bound references untouched and silently test nothing.
        monkeypatch.setattr(example_service, "ActivationRepository", lambda _db: activation_repo)
        monkeypatch.setattr(
            example_service,
            "ActivationService",
            lambda *_a, **_kw: MagicMock(end=AsyncMock(side_effect=_end)),
        )

        project_id = uuid.uuid4()
        with patch("contexts.activities.application.example_service.audit.emit", new=AsyncMock()):
            ended = await svc.opt_out(
                project_id=project_id,
                activity_type_id=installed.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        mine = next(a for a in activations if a.chatroom_id == my_room)
        theirs = next(a for a in activations if a.chatroom_id == their_room)
        assert ended == [(my_room, mine.id)]
        assert theirs.id not in ended_ids

        # Sessions are closed room-by-room too, never with the unbounded
        # close_open_for_type that the admin delete uses.
        svc._session_repo.close_open_for_type_in_rooms.assert_awaited_once_with(installed.id, [my_room])

    async def test_opting_out_of_something_not_enabled_is_refused(self) -> None:
        installed = _platform_type("mandala-9grid")
        svc = _service(platform_types=[installed])
        svc._optin_repo.remove = AsyncMock(return_value=False)

        with pytest.raises(ActivityTypeNotOptedIn):
            await svc.opt_out(
                project_id=uuid.uuid4(),
                activity_type_id=installed.id,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        svc._session_repo.close_open_for_type_in_rooms.assert_not_awaited()
