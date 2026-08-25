"""`group_config` at registration and at edit — AC-3.

The consent fraction is a **behavioural** definition field ([R30.40]), not safe
metadata: an edit bumps the type's version and is refused while any activation of
it is live, because a threshold that moved under an open proposal would change
what a group already agreed to clear. This file pins that classification, the
validation on both write paths, and the one place the field is deliberately NOT
editable.

DB is mocked, in the style of ``test_activity_type_edit``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.domain.errors import ActivityTypeActive, GroupConfigInvalid
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"case": {"type": "string"}}, "required": ["case"]}
_TWO_THIRDS: dict[str, Any] = {"consent": {"numerator": 2, "denominator": 3}}
_HALF: dict[str, Any] = {"consent": {"numerator": 1, "denominator": 2}}
_AUDIT = "contexts.activities.application.type_service.audit.emit"


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "six-hats-shared-case",
        "name": "Shared case",
        "payload_schema": _SCHEMA,
        # A webhook validator, so this file needs no entry in the process-global
        # in-process registry — which is populated at startup, not at import
        # (the same reason ``test_activity_type_edit`` uses one).
        "validator_kind": ValidatorKind.WEBHOOK,
        "validator_config": {"url": "https://example.test/score"},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
    }
    base.update(over)
    return ActivityType(**base)


def _active(type_id: uuid.UUID) -> ActivityActivation:
    return ActivityActivation(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        activity_type_id=type_id,
        started_by_user_id=uuid.uuid4(),
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _wire(existing: ActivityType | None, *, active: list[ActivityActivation] | None = None) -> Any:
    svc = ActivityTypeService(MagicMock())
    svc._repo = MagicMock()  # type: ignore[assignment]
    reloaded = _make_type(id=existing.id, project_id=existing.project_id) if existing else None
    svc._repo.get = AsyncMock(side_effect=[existing, reloaded])
    svc._repo.update = AsyncMock(return_value=True)
    svc._repo.create = AsyncMock(return_value=uuid.uuid4())
    svc._repo.list_platform_by_keys = AsyncMock(return_value=[])
    svc._activation_repo = MagicMock()  # type: ignore[assignment]
    svc._activation_repo.list_active_for_type = AsyncMock(return_value=active or [])
    svc._policy._repo = MagicMock()
    svc._policy._repo.get_platform = AsyncMock(return_value=None)
    return svc


async def _register(svc: Any, group_config: dict[str, Any] | None) -> None:
    with patch(_AUDIT, new=AsyncMock()):
        await svc.register(
            project_id=uuid.uuid4(),
            key="six-hats-shared-case",
            name="Shared case",
            payload_schema=_SCHEMA,
            validator_kind=ValidatorKind.WEBHOOK,
            validator_config={"url": "https://example.test/score"},
            retention_days=None,
            group_config=group_config,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )


async def _update(svc: Any, existing: ActivityType, **over: Any) -> ActivityType:
    body: dict[str, Any] = {
        "name": existing.name,
        "payload_schema": existing.payload_schema,
        "validator_kind": existing.validator_kind,
        "validator_config": existing.validator_config,
        "retention_days": existing.retention_days,
        "group_config": existing.group_config,
    }
    body.update(over)
    with patch(_AUDIT, new=AsyncMock()):
        return await svc.update(
            project_id=existing.project_id,
            type_id=existing.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            **body,
        )


class TestRegistration:
    async def test_a_valid_fraction_is_stored(self) -> None:
        svc = _wire(None)
        svc._repo.get = AsyncMock(return_value=_make_type(group_config=_TWO_THIRDS))

        await _register(svc, _TWO_THIRDS)

        assert svc._repo.create.await_args.kwargs["group_config"] == _TWO_THIRDS

    async def test_no_fraction_means_individual_only(self) -> None:
        """AC-2: every type that exists today registers exactly as it did."""
        svc = _wire(None)
        svc._repo.get = AsyncMock(return_value=_make_type())

        await _register(svc, None)

        assert svc._repo.create.await_args.kwargs["group_config"] is None

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param({"consent": {"numerator": 4, "denominator": 3}}, id="above-one"),
            pytest.param({"consent": {"numerator": 0, "denominator": 3}}, id="zero"),
            pytest.param({"consent": {"numerator": "2", "denominator": 3}}, id="not-an-integer"),
            pytest.param({"threshold": 0.66}, id="wrong-shape"),
        ],
    )
    async def test_a_malformed_fraction_is_refused_before_anything_is_written(
        self, bad: dict[str, Any]
    ) -> None:
        svc = _wire(None)

        with pytest.raises(GroupConfigInvalid):
            await _register(svc, bad)

        svc._repo.create.assert_not_awaited()


class TestEditing:
    async def test_changing_the_fraction_bumps_the_version(self) -> None:
        """It governs a vote, so it belongs on the behavioural side of [R30.23] —
        the same side as `payload_schema`, not the same side as `name`."""
        existing = _make_type(group_config=_TWO_THIRDS)
        svc = _wire(existing)

        await _update(svc, existing, group_config=_HALF)

        assert svc._repo.update.await_args.kwargs["bump_version"] is True
        assert svc._repo.update.await_args.kwargs["group_config"] == _HALF

    async def test_adding_a_fraction_to_an_individual_type_bumps_the_version(self) -> None:
        existing = _make_type(group_config=None)
        svc = _wire(existing)

        await _update(svc, existing, group_config=_TWO_THIRDS)

        assert svc._repo.update.await_args.kwargs["bump_version"] is True

    async def test_leaving_it_alone_does_not(self) -> None:
        """A metadata-only edit is still a metadata-only edit."""
        existing = _make_type(group_config=_TWO_THIRDS)
        svc = _wire(existing)

        await _update(svc, existing, name="Renamed")

        assert svc._repo.update.await_args.kwargs["bump_version"] is False

    async def test_the_fraction_cannot_move_while_a_round_is_running(self) -> None:
        """AC-3's second half, and the reason `group_config` is behavioural: a
        threshold changing under an open proposal would move a bar the group had
        already agreed to clear."""
        existing = _make_type(group_config=_TWO_THIRDS)
        svc = _wire(existing, active=[_active(existing.id)])

        with pytest.raises(ActivityTypeActive):
            await _update(svc, existing, group_config=_HALF)

        svc._repo.update.assert_not_awaited()

    async def test_a_malformed_fraction_is_refused_on_edit_too(self) -> None:
        existing = _make_type(group_config=_TWO_THIRDS)
        svc = _wire(existing)

        with pytest.raises(GroupConfigInvalid):
            await _update(svc, existing, group_config={"consent": {"numerator": 9, "denominator": 3}})

        svc._repo.update.assert_not_awaited()


class TestTheAdminInstallSurfaceStillRefusesIt:
    async def test_a_platform_edit_carries_the_stored_fraction_through_unchanged(self) -> None:
        """`AdminPlatformActivityTypeIn` is a four-field install surface and
        deliberately does not gain `group_config` (§6 of the dossier). The trap
        this pins is the other one: the admin path reuses the same repository
        `update`, so omitting the field there would NULL the fraction on every
        governance edit of a shipped example.
        """
        from contexts.activities.domain.models import ActivityTypeScope

        platform = _make_type(project_id=None, scope=ActivityTypeScope.PLATFORM, group_config=_TWO_THIRDS)
        svc = _wire(platform)

        with patch(_AUDIT, new=AsyncMock()):
            await svc.update_platform_type(
                type_id=platform.id,
                name="Shared case (no agent exposure)",
                retention_days=30,
                expose_payload_to_agent=False,
                echo_includes_content=False,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert svc._repo.update.await_args.kwargs["group_config"] == _TWO_THIRDS
        assert svc._repo.update.await_args.kwargs["bump_version"] is False
