"""AC-5/AC-6: a platform type is reachable from a project only after opt-in.

This is the regression net for the security consideration the dossier calls out
first. Filtering ``list_for_project`` is a presentation change; all three
room-level services take an ``activity_type_id`` straight from the client body
(``app/api/v1/activities.py`` — activations, sessions, submissions), so each one
is independently load-bearing. A refactor that taught the *listing* about
platform types while leaving any one of these three comparing ``project_id``
would let a facilitator in any org start any installed example by id.

Every case is therefore parametrized over all three services rather than tested
once against whichever one happens to be convenient.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.activities.application.activation_service import ActivationService
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.application.session_service import ActivitySessionService
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.domain.errors import ActivityTypeNotFound
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ActivityTypeScope,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 8, 9, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "mandala-9grid",
        "name": "Mandala",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 1},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "deleted_at": None,
    }
    base.update(over)
    return ActivityType(**base)


def _platform_type(**over: Any) -> ActivityType:
    return _make_type(project_id=None, scope=ActivityTypeScope.PLATFORM, **over)


def _optin_reader(*, opted_in: bool) -> MagicMock:
    return MagicMock(exists=AsyncMock(return_value=opted_in))


# --------------------------------------------------------------------------- #
# The shared predicate + resolver                                              #
# --------------------------------------------------------------------------- #


class TestVisibilityPredicate:
    """``ActivityType.is_visible_to`` — pure, so it can be exhausted cheaply."""

    def test_a_project_type_is_visible_to_its_own_project(self) -> None:
        project_id = uuid.uuid4()
        assert _make_type(project_id=project_id).is_visible_to(project_id, opted_in=False)

    def test_a_project_type_is_invisible_to_another_project(self) -> None:
        assert not _make_type().is_visible_to(uuid.uuid4(), opted_in=False)

    def test_an_optin_does_not_admit_another_projects_type(self) -> None:
        """A stray opt-in row must not override project ownership.

        The opt-in table is keyed on platform types, but nothing at the type level
        stops a row naming a project-scoped id — so the predicate must not treat
        ``opted_in`` as a universal override.
        """
        assert not _make_type().is_visible_to(uuid.uuid4(), opted_in=True)

    def test_a_platform_type_needs_an_optin(self) -> None:
        platform = _platform_type()
        assert not platform.is_visible_to(uuid.uuid4(), opted_in=False)
        assert platform.is_visible_to(uuid.uuid4(), opted_in=True)


class TestResolveReachableType:
    async def test_a_missing_type_is_not_found(self) -> None:
        with pytest.raises(ActivityTypeNotFound):
            await resolve_reachable_type(
                type_reader=MagicMock(get=AsyncMock(return_value=None)),
                optin_reader=_optin_reader(opted_in=True),
                activity_type_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
            )

    async def test_the_optin_table_is_not_read_for_a_project_type(self) -> None:
        """The common path must cost no extra query — this runs on every submit."""
        project_id = uuid.uuid4()
        activity_type = _make_type(project_id=project_id)
        optins = _optin_reader(opted_in=False)

        resolved = await resolve_reachable_type(
            type_reader=MagicMock(get=AsyncMock(return_value=activity_type)),
            optin_reader=optins,
            activity_type_id=activity_type.id,
            project_id=project_id,
        )

        assert resolved == activity_type
        optins.exists.assert_not_awaited()

    async def test_the_optin_is_looked_up_for_the_calling_project(self) -> None:
        """Not for the type's own project — a platform type has none, and asking
        about the wrong project is how a global allowance sneaks in."""
        caller_project = uuid.uuid4()
        activity_type = _platform_type()
        optins = _optin_reader(opted_in=True)

        await resolve_reachable_type(
            type_reader=MagicMock(get=AsyncMock(return_value=activity_type)),
            optin_reader=optins,
            activity_type_id=activity_type.id,
            project_id=caller_project,
        )

        optins.exists.assert_awaited_once_with(project_id=caller_project, activity_type_id=activity_type.id)


# --------------------------------------------------------------------------- #
# All three room-level services                                                #
# --------------------------------------------------------------------------- #


def _activation(chatroom_id: uuid.UUID, activity_type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        activity_type_id=activity_type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


@dataclass
class _Entry:
    """One room-level path under test.

    ``probe`` is the first collaborator the service reaches *after* the gate, so
    "was it awaited" answers "did the request get past reachability" without this
    file having to mock out each service's full happy path — those are covered in
    ``test_activities_services.py``. It also sharpens the negative cases: a
    refusal must happen before any side effect, not after one.
    """

    call: Callable[[], Awaitable[object]]
    probe: AsyncMock


def _start_activation(*, activity_type: ActivityType, project_id: uuid.UUID, opted_in: bool) -> _Entry:
    probe = AsyncMock(return_value=None)
    svc = ActivationService(
        MagicMock(),
        activation_repo=MagicMock(create_active=probe, get_active=AsyncMock(return_value=None)),
        type_repo=MagicMock(get=AsyncMock(return_value=activity_type)),
        optin_repo=_optin_reader(opted_in=opted_in),
    )
    # No policy row -> permissive, so the only gate under test is reachability.
    svc._policy._repo = MagicMock(get_platform=AsyncMock(return_value=None))

    async def _call() -> object:
        return await svc.start(
            project_id=project_id,
            chatroom_id=uuid.uuid4(),
            activity_type_id=activity_type.id,
            started_by_user_id=uuid.uuid4(),
            actor_ip=None,
        )

    return _Entry(call=_call, probe=probe)


def _open_session(*, activity_type: ActivityType, project_id: uuid.UUID, opted_in: bool) -> _Entry:
    probe = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    svc = ActivitySessionService(MagicMock())
    svc._type_repo = MagicMock(get=AsyncMock(return_value=activity_type))
    svc._optin_repo = _optin_reader(opted_in=opted_in)
    svc._repo = MagicMock(get_open=probe)

    subject = uuid.uuid4()

    async def _call() -> object:
        return await svc.open_session(
            project_id=project_id,
            activity_type_id=activity_type.id,
            chatroom_id=uuid.uuid4(),
            subject_user_id=subject,
            caller_user_id=subject,
        )

    return _Entry(call=_call, probe=probe)


def _submit(*, activity_type: ActivityType, project_id: uuid.UUID, opted_in: bool) -> _Entry:
    chatroom_id = uuid.uuid4()
    probe = AsyncMock(return_value=_activation(chatroom_id, activity_type.id))
    svc = SubmissionService(MagicMock(), activation_repo=MagicMock(get_active_for_update=probe))
    svc._type_repo = MagicMock(get=AsyncMock(return_value=activity_type))
    svc._optin_repo = _optin_reader(opted_in=opted_in)

    subject = uuid.uuid4()

    async def _call() -> object:
        return await svc.submit(
            project_id=project_id,
            activity_type_id=activity_type.id,
            chatroom_id=chatroom_id,
            producer_user_id=subject,
            subject_user_id=subject,
            caller_user_id=subject,
            payload={"answer": "x"},
            actor_user_id=subject,
            actor_ip=None,
        )

    return _Entry(call=_call, probe=probe)


_ENTRY_POINTS = [
    pytest.param(_start_activation, id="activation"),
    pytest.param(_open_session, id="session"),
    pytest.param(_submit, id="submission"),
]

_EntryBuilder = Callable[..., _Entry]


async def _run_past_the_gate(entry: _Entry) -> None:
    """Invoke, letting a gate refusal through and ignoring anything after it.

    ``ActivityTypeNotFound`` is re-raised because it *is* the subject; every other
    exception is a downstream mechanic this file deliberately does not mock (the
    session insert, the SYSTEM echo, the audit write), and swallowing it here
    costs nothing because ``entry.probe`` already proves the gate was passed.
    """
    try:
        await entry.call()
    except ActivityTypeNotFound:
        raise
    except Exception:
        # See the docstring: the probe carries the assertion, so a downstream
        # failure here is not this test's subject.
        pass


@pytest.mark.parametrize("entry_point", _ENTRY_POINTS)
class TestPlatformTypeReachability:
    """AC-5/AC-6, across every path that accepts a client-supplied type id."""

    async def test_a_platform_type_is_refused_before_optin(self, entry_point: _EntryBuilder) -> None:
        entry = entry_point(activity_type=_platform_type(), project_id=uuid.uuid4(), opted_in=False)

        with pytest.raises(ActivityTypeNotFound):
            await entry.call()

        entry.probe.assert_not_awaited()

    async def test_a_non_opted_in_project_is_refused_even_though_it_is_installed(
        self, entry_point: _EntryBuilder
    ) -> None:
        """AC-6: installed platform-wide is not the same as enabled here.

        Identical mechanics to the case above, and stated separately because it is
        the cross-tenant claim: the type genuinely exists and another project is
        already using it.
        """
        entry = entry_point(activity_type=_platform_type(), project_id=uuid.uuid4(), opted_in=False)

        with pytest.raises(ActivityTypeNotFound):
            await entry.call()

        entry.probe.assert_not_awaited()

    async def test_a_cross_project_type_is_still_refused(self, entry_point: _EntryBuilder) -> None:
        """The pre-0076 guarantee, re-asserted through the new code path."""
        entry = entry_point(activity_type=_make_type(), project_id=uuid.uuid4(), opted_in=False)

        with pytest.raises(ActivityTypeNotFound):
            await entry.call()

        entry.probe.assert_not_awaited()

    async def test_an_optin_admits_the_platform_type(self, entry_point: _EntryBuilder) -> None:
        """The positive half — without it the three tests above would pass just as
        happily on a service that refuses everything."""
        entry = entry_point(activity_type=_platform_type(), project_id=uuid.uuid4(), opted_in=True)

        await _run_past_the_gate(entry)

        entry.probe.assert_awaited()

    async def test_a_project_reaches_its_own_type(self, entry_point: _EntryBuilder) -> None:
        """And the unchanged case still works — no opt-in required."""
        project_id = uuid.uuid4()
        entry = entry_point(
            activity_type=_make_type(project_id=project_id), project_id=project_id, opted_in=False
        )

        await _run_past_the_gate(entry)

        entry.probe.assert_awaited()
