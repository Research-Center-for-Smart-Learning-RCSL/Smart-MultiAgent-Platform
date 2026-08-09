"""`ActivitiesFacade.preview_policy_impact` — what a tightening would cost.

The preview is the only thing standing between an admin and a platform-wide
change whose damage otherwise appears when a facilitator cannot start a class
([R30.30]). Two properties it has to hold: it must not bless a policy the writer
will reject, and it must count the activities running *right now*, which the two
enforcement gates (authoring, activation start) do not stop.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contexts.activities.domain.errors import ActivityPolicyInconsistent
from contexts.activities.domain.models import (
    PLATFORM_SCOPE,
    ActivationStatus,
    ActivityActivation,
    ActivityPolicy,
    ActivityType,
    ActivityTypeScope,
    ValidatorKind,
)
from contexts.activities.interfaces.facade import ActivitiesFacade

_NOW = dt.datetime(2026, 8, 9, tzinfo=dt.UTC)


def _policy(**over: Any) -> ActivityPolicy:
    base: dict[str, Any] = {
        "id": None,
        "scope": PLATFORM_SCOPE,
        "expose_payload_to_agent_default": True,
        "expose_payload_to_agent_locked": False,
        "echo_includes_content_default": False,
        "echo_includes_content_locked": False,
        "retention_days_default": None,
        "retention_days_max": None,
        "version": 0,
    }
    base.update(over)
    return ActivityPolicy(**base)


def _type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "probe",
        "name": "Probe",
        "payload_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 0},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
    }
    base.update(over)
    return ActivityType(**base)


def _activation(activity_type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        activity_type_id=activity_type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _facade(
    *,
    types: list[ActivityType] | None = None,
    activations: list[ActivityActivation] | None = None,
) -> ActivitiesFacade:
    facade = ActivitiesFacade(MagicMock())
    running = activations or []
    facade._type_repo = MagicMock()
    facade._type_repo.list_all = AsyncMock(return_value=types or [])
    facade._activation_repo = MagicMock()
    facade._activation_repo.list_all_active = AsyncMock(return_value=running)

    by_id = {at.id: at for at in (types or [])}

    async def _get_many(ids: list[uuid.UUID]) -> dict[uuid.UUID, ActivityType]:
        return {i: by_id[i] for i in ids if i in by_id}

    facade._type_repo.get_many = AsyncMock(side_effect=_get_many)
    return facade


class TestSelfConsistency:
    async def test_a_contradictory_candidate_is_rejected_not_previewed(self) -> None:
        """`default=500, max=100` passes Pydantic and both table CHECKs, so the
        preview is where an admin would otherwise be told it is fine — and then
        get a 422 on save from the same numbers."""
        facade = _facade()

        with pytest.raises(ActivityPolicyInconsistent):
            await facade.preview_policy_impact(_policy(retention_days_default=500, retention_days_max=100))

        facade._type_repo.list_all.assert_not_awaited()

    async def test_a_consistent_candidate_is_previewed(self) -> None:
        impact = await _facade().preview_policy_impact(
            _policy(retention_days_default=30, retention_days_max=90)
        )

        assert impact.violating_types == 0
        assert impact.violating_activations == 0


class TestPlatformScopedTypesAreCounted:
    """AC-9: an admin tightening a policy must be told it breaks the examples.

    `list_all` is deliberately scope-blind, so platform rows are in the scan
    already — but that is exactly the kind of property a later "only count a
    project's own types" filter would quietly remove, stranding the shipped
    examples with the admin having been told nothing would break.
    """

    async def test_a_platform_type_counts_among_the_violations(self) -> None:
        facade = _facade(
            types=[
                _type(
                    key="mandala-9grid",
                    project_id=None,
                    scope=ActivityTypeScope.PLATFORM,
                    expose_payload_to_agent=True,
                )
            ]
        )

        impact = await facade.preview_policy_impact(
            _policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True)
        )

        assert impact.violating_types == 1

    async def test_a_running_platform_activation_counts_too(self) -> None:
        platform = _type(
            key="mandala-9grid",
            project_id=None,
            scope=ActivityTypeScope.PLATFORM,
            expose_payload_to_agent=True,
        )
        facade = _facade(types=[platform], activations=[_activation(platform.id)])

        impact = await facade.preview_policy_impact(
            _policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True)
        )

        assert impact.violating_activations == 1


class TestCountsViolatingTypes:
    async def test_counts_only_the_types_the_candidate_would_refuse(self) -> None:
        facade = _facade(
            types=[
                _type(key="exposes", expose_payload_to_agent=True),
                _type(key="hides", expose_payload_to_agent=False),
            ]
        )

        impact = await facade.preview_policy_impact(
            _policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True)
        )

        assert impact.violating_types == 1
        assert impact.approximate is False


class TestCountsRunningActivations:
    """The number an admin tightening for a consent reason has to see."""

    async def test_counts_a_running_activity_whose_type_would_be_refused(self) -> None:
        exposing = _type(key="exposes", expose_payload_to_agent=True)
        compliant = _type(key="hides", expose_payload_to_agent=False)
        facade = _facade(
            types=[exposing, compliant],
            activations=[_activation(exposing.id), _activation(compliant.id)],
        )

        impact = await facade.preview_policy_impact(
            _policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True)
        )

        assert impact.violating_activations == 1

    async def test_a_permissive_candidate_refuses_nothing(self) -> None:
        exposing = _type(expose_payload_to_agent=True)
        facade = _facade(types=[exposing], activations=[_activation(exposing.id)])

        impact = await facade.preview_policy_impact(_policy())

        assert impact.violating_types == 0
        assert impact.violating_activations == 0

    async def test_an_activation_whose_type_is_gone_is_not_counted(self) -> None:
        """A deleted type cannot breach anything, and must not 500 the preview."""
        facade = _facade(types=[], activations=[_activation(uuid.uuid4())])

        impact = await facade.preview_policy_impact(
            _policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True)
        )

        assert impact.violating_activations == 0
