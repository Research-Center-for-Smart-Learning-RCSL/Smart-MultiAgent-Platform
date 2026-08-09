"""The two policy enforcement points ([R30.30]).

AC-8 (authoring is gated) and AC-9 (activation is gated, and the stored row is
never rewritten). These use the real services with mocked repositories, so the
gate ordering inside `register`/`update`/`start` actually executes — a test that
mocked the service would prove nothing about where the check sits.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application.activation_service import ActivationService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.domain.errors import ActivityTypeViolatesPolicy
from contexts.activities.domain.models import PLATFORM_SCOPE, ActivityPolicy, ActivityType, ValidatorKind

_NOW = dt.datetime(2026, 8, 9, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def _locked_policy(**over: Any) -> ActivityPolicy:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "scope": PLATFORM_SCOPE,
        "expose_payload_to_agent_default": False,
        "expose_payload_to_agent_locked": True,
        "echo_includes_content_default": False,
        "echo_includes_content_locked": False,
        "retention_days_default": None,
        "retention_days_max": None,
        "version": 2,
        "updated_at": _NOW,
        "updated_by_user_id": None,
    }
    base.update(over)
    return ActivityPolicy(**base)


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "k",
        "name": "n",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "filled_count", "min_filled": 0},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "expose_payload_to_agent": True,
        "echo_includes_content": False,
        "deleted_at": None,
    }
    base.update(over)
    return ActivityType(**base)


def _type_service(policy: ActivityPolicy) -> ActivityTypeService:
    svc = ActivityTypeService(MagicMock())
    svc._repo = MagicMock()
    svc._repo.create = AsyncMock()
    svc._repo.update = AsyncMock()
    svc._activation_repo = MagicMock()
    svc._activation_repo.list_active_for_type = AsyncMock(return_value=[])
    svc._policy._repo = MagicMock()
    svc._policy._repo.get_platform = AsyncMock(return_value=policy)
    return svc


class TestAuthoringGate:
    async def test_register_is_rejected_when_it_violates_the_policy(self) -> None:
        """AC-8."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _type_service(_locked_policy())

        with pytest.raises(ActivityTypeViolatesPolicy) as exc:
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                expose_payload_to_agent=True,
            )

        assert exc.value.field == "expose_payload_to_agent"
        # Rejected before the write, so no partial row and no audit event.
        svc._repo.create.assert_not_awaited()

    async def test_register_passes_when_it_matches(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _type_service(_locked_policy())
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id, expose_payload_to_agent=False))

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                expose_payload_to_agent=False,
            )

        svc._repo.create.assert_awaited_once()

    async def test_edit_of_only_the_governance_field_is_still_gated(self) -> None:
        """The bypass this ordering exists to close.

        The three governance fields are safe metadata under R30.23, so flipping
        one does not make the edit "behavioral". A gate placed inside the
        behavioral branch would let an owner turn agent exposure back on by
        editing nothing else.
        """
        project_id, type_id = uuid.uuid4(), uuid.uuid4()
        svc = _type_service(_locked_policy())
        # Identical schema/validator => behavioral_changed is False.
        svc._repo.get = AsyncMock(
            return_value=_make_type(id=type_id, project_id=project_id, expose_payload_to_agent=False)
        )

        with pytest.raises(ActivityTypeViolatesPolicy):
            await svc.update(
                project_id=project_id,
                type_id=type_id,
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                expose_payload_to_agent=True,
            )

        svc._repo.update.assert_not_awaited()


class TestActivationGate:
    def _service(self, policy: ActivityPolicy, activity_type: ActivityType) -> ActivationService:
        activation_repo = MagicMock()
        activation_repo.create_active = AsyncMock()
        type_repo = MagicMock()
        type_repo.get = AsyncMock(return_value=activity_type)
        svc = ActivationService(MagicMock(), activation_repo=activation_repo, type_repo=type_repo)
        svc._policy._repo = MagicMock()
        svc._policy._repo.get_platform = AsyncMock(return_value=policy)
        return svc

    async def test_activating_a_pre_existing_violating_type_is_refused(self) -> None:
        """AC-9: this is what gives a tightened policy reach over the installed base."""
        project_id = uuid.uuid4()
        at = _make_type(project_id=project_id, expose_payload_to_agent=True)
        svc = self._service(_locked_policy(), at)

        with pytest.raises(ActivityTypeViolatesPolicy) as exc:
            await svc.start(
                project_id=project_id,
                chatroom_id=uuid.uuid4(),
                activity_type_id=at.id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert exc.value.field == "expose_payload_to_agent"
        # No activation row, no audit event, no room broadcast.
        svc._repo.create_active.assert_not_awaited()

    async def test_the_stored_type_is_not_modified(self) -> None:
        """AC-9: the platform never rewrites a type to match a policy change."""
        project_id = uuid.uuid4()
        at = _make_type(project_id=project_id, expose_payload_to_agent=True)
        svc = self._service(_locked_policy(), at)

        with pytest.raises(ActivityTypeViolatesPolicy):
            await svc.start(
                project_id=project_id,
                chatroom_id=uuid.uuid4(),
                activity_type_id=at.id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        # The repository handed to the service is read-only in this flow; assert
        # nothing tried to write through it.
        assert not [c for c in svc._type_repo.mock_calls if "update" in str(c)]

    async def test_a_compliant_type_still_activates(self) -> None:
        project_id, activation_id = uuid.uuid4(), uuid.uuid4()
        at = _make_type(project_id=project_id, expose_payload_to_agent=False)
        svc = self._service(_locked_policy(), at)
        svc._repo.create_active = AsyncMock(return_value=activation_id)
        svc._repo.get = AsyncMock(return_value=MagicMock(id=activation_id, activity_type_id=at.id))

        with patch("contexts.activities.application.activation_service.audit.emit", new=AsyncMock()):
            await svc.start(
                project_id=project_id,
                chatroom_id=uuid.uuid4(),
                activity_type_id=at.id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        svc._repo.create_active.assert_awaited_once()

    async def test_a_permissive_policy_does_not_block_activation(self) -> None:
        """AC-7 at the activation gate: nothing changes until an admin tightens."""
        project_id, activation_id = uuid.uuid4(), uuid.uuid4()
        at = _make_type(project_id=project_id, expose_payload_to_agent=True)
        svc = self._service(None, at)  # type: ignore[arg-type]
        svc._policy._repo.get_platform = AsyncMock(return_value=None)
        svc._repo.create_active = AsyncMock(return_value=activation_id)
        svc._repo.get = AsyncMock(return_value=MagicMock(id=activation_id, activity_type_id=at.id))

        with patch("contexts.activities.application.activation_service.audit.emit", new=AsyncMock()):
            await svc.start(
                project_id=project_id,
                chatroom_id=uuid.uuid4(),
                activity_type_id=at.id,
                started_by_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        svc._repo.create_active.assert_awaited_once()
