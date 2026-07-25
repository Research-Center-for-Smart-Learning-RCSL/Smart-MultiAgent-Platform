"""Edit an existing activity type (R30.23): in-place PATCH of metadata +
behavioral fields, a version bump only on a behavioral change, and a 409 when a
behavioral edit is attempted while the type is active.

DB is mocked (repo instances replaced): pins the version-bump decision, the
active-guard, the tenant guard, and the route's owner + mcp-scope checks — no
Postgres required. Mirrors the mocked-repo style of ``test_activities_services.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import activities
from app.api.v1.activities import ActivityTypeUpdateIn
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.domain.errors import (
    ActivityTypeActive,
    ActivityTypeNotFound,
    PayloadSchemaInvalid,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivityType,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 7, 23, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "quiz",
        "name": "Quiz",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.WEBHOOK,
        "validator_config": {"url": "https://example.test/score"},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "deleted_at": None,
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


def _wire_service(
    existing: ActivityType | None, *, active: list[ActivityActivation] | None = None
) -> ActivityTypeService:
    svc = ActivityTypeService(MagicMock())
    svc._repo = MagicMock()
    # First get() returns the pre-edit row; the second returns the post-edit row.
    reloaded = _make_type(id=existing.id, project_id=existing.project_id) if existing else None
    svc._repo.get = AsyncMock(side_effect=[existing, reloaded])
    svc._repo.update = AsyncMock(return_value=True)
    svc._activation_repo = MagicMock()
    svc._activation_repo.list_active_for_type = AsyncMock(return_value=active or [])
    return svc


async def _update(svc: ActivityTypeService, existing: ActivityType, **over: Any) -> ActivityType:
    body: dict[str, Any] = {
        "name": existing.name,
        "payload_schema": existing.payload_schema,
        "validator_kind": existing.validator_kind,
        "validator_config": existing.validator_config,
        "retention_days": existing.retention_days,
    }
    body.update(over)
    with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()) as emit:
        result = await svc.update(
            project_id=existing.project_id,
            type_id=existing.id,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
            **body,
        )
    svc._emit = emit  # type: ignore[attr-defined]  # exposed for assertions
    return result


class TestUpdateService:
    async def test_metadata_only_edit_does_not_bump_version(self) -> None:
        """AC-6: a name-only change leaves version untouched (bump_version=False)
        and never consults the active guard."""
        existing = _make_type()
        svc = _wire_service(existing)

        await _update(svc, existing, name="Quiz v2")

        kwargs = svc._repo.update.await_args.kwargs
        assert kwargs["bump_version"] is False
        assert kwargs["name"] == "Quiz v2"
        svc._activation_repo.list_active_for_type.assert_not_awaited()
        svc._emit.assert_awaited_once()  # type: ignore[attr-defined]  # AC-3

    async def test_agent_visibility_toggles_are_safe_metadata(self) -> None:
        """Agent-visibility follow-up: flipping expose_payload_to_agent/
        echo_includes_content alone is metadata-only — no version bump, no active
        guard, even while an activation is live."""
        existing = _make_type()
        svc = _wire_service(existing, active=[_active(existing.id)])

        await _update(svc, existing, expose_payload_to_agent=False, echo_includes_content=True)

        kwargs = svc._repo.update.await_args.kwargs
        assert kwargs["bump_version"] is False
        assert kwargs["expose_payload_to_agent"] is False
        assert kwargs["echo_includes_content"] is True
        svc._activation_repo.list_active_for_type.assert_not_awaited()

    async def test_behavioral_edit_bumps_version(self) -> None:
        """AC-6: a payload_schema change bumps version and re-runs validation."""
        existing = _make_type()
        svc = _wire_service(existing)
        new_schema = {"type": "object", "properties": {"answer": {"type": "number"}}}

        await _update(svc, existing, payload_schema=new_schema)

        kwargs = svc._repo.update.await_args.kwargs
        assert kwargs["bump_version"] is True
        assert kwargs["payload_schema"] == new_schema

    async def test_behavioral_edit_while_active_is_rejected(self) -> None:
        """AC-7: a behavioral edit is blocked (409) while the type has an active
        activation; nothing is written."""
        existing = _make_type()
        svc = _wire_service(existing, active=[_active(existing.id)])
        new_schema = {"type": "object", "properties": {"answer": {"type": "number"}}}

        with pytest.raises(ActivityTypeActive):
            await _update(svc, existing, payload_schema=new_schema)

        svc._repo.update.assert_not_awaited()

    async def test_metadata_edit_while_active_succeeds(self) -> None:
        """AC-7: a metadata-only edit is allowed even while active — the active
        guard is never reached."""
        existing = _make_type()
        svc = _wire_service(existing, active=[_active(existing.id)])

        await _update(svc, existing, name="Renamed while live")

        svc._repo.update.assert_awaited_once()
        svc._activation_repo.list_active_for_type.assert_not_awaited()

    async def test_unknown_type_raises_and_writes_nothing(self) -> None:
        """AC-2: editing a missing/soft-deleted type is a 404."""
        svc = _wire_service(None)
        svc._repo.get = AsyncMock(return_value=None)

        with pytest.raises(ActivityTypeNotFound):
            await svc.update(
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                name="x",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.WEBHOOK,
                validator_config={"url": "https://example.test/s"},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.update.assert_not_awaited()

    async def test_cross_project_type_is_refused(self) -> None:
        """AC-2: a type belonging to another project is a 404, not an edit."""
        existing = _make_type(project_id=uuid.uuid4())
        svc = _wire_service(existing)

        with pytest.raises(ActivityTypeNotFound):
            await svc.update(
                project_id=uuid.uuid4(),  # not the type's project
                type_id=existing.id,
                name="x",
                payload_schema=existing.payload_schema,
                validator_kind=existing.validator_kind,
                validator_config=existing.validator_config,
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.update.assert_not_awaited()

    async def test_behavioral_edit_revalidates_validator_config(self) -> None:
        """A behavioral edit re-runs the register-time validator check; an empty
        webhook config is rejected before any write."""
        existing = _make_type()
        svc = _wire_service(existing)

        with pytest.raises(ValidatorConfigInvalid):
            await _update(svc, existing, validator_config={})

        svc._repo.update.assert_not_awaited()

    async def test_behavioral_edit_revalidates_schema(self) -> None:
        existing = _make_type()
        svc = _wire_service(existing)

        with pytest.raises(PayloadSchemaInvalid):
            await _update(svc, existing, payload_schema={"type": "nonsense"})

        svc._repo.update.assert_not_awaited()


def _body(**over: Any) -> ActivityTypeUpdateIn:
    base: dict[str, Any] = {
        "name": "Quiz",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.WEBHOOK,
        "validator_config": {"url": "https://example.test/score"},
        "retention_days": None,
    }
    base.update(over)
    return ActivityTypeUpdateIn(**base)


class TestUpdateRoute:
    async def test_owner_update_commits_and_returns_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-1: an owner's edit commits and returns the updated row."""
        updated = _make_type()
        facade = MagicMock()
        facade.update_type = AsyncMock(return_value=updated)
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())

        out = await activities.update_activity_type(
            body=_body(name="Renamed"),
            project_id=updated.project_id,
            type_id=updated.id,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=SimpleNamespace(user_id=uuid.uuid4()),
            db=db,
        )

        facade.update_type.assert_awaited_once()
        db.commit.assert_awaited_once()
        assert out.id == updated.id

    async def test_non_owner_is_refused_before_any_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-1: a non-owner gets 403 and nothing is written."""
        facade = MagicMock()
        facade.update_type = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(
            activities, "assert_project_owner", AsyncMock(side_effect=HTTPException(status_code=403))
        )

        with pytest.raises(HTTPException) as exc:
            await activities.update_activity_type(
                body=_body(),
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=uuid.uuid4()),
                db=db,
            )

        assert exc.value.status_code == 403
        facade.update_type.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_mcp_foreign_binding_is_rejected_before_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-4: a PATCH moving to an mcp config with a foreign binding is rejected
        (422) by the same route guard registration uses; no write, no commit."""
        facade = MagicMock()
        facade.update_type = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()
        monkeypatch.setattr(activities, "ActivitiesFacade", lambda _db: facade)
        monkeypatch.setattr(activities, "assert_project_owner", AsyncMock())
        monkeypatch.setattr(
            activities,
            "_assert_mcp_binding_in_project",
            AsyncMock(side_effect=ValidatorConfigInvalid("foreign binding")),
        )

        body = _body(
            validator_kind=ValidatorKind.MCP,
            validator_config={
                "agent_id": str(uuid.uuid4()),
                "binding_id": str(uuid.uuid4()),
                "tool_name": "score",
            },
        )
        with pytest.raises(ValidatorConfigInvalid):
            await activities.update_activity_type(
                body=body,
                project_id=uuid.uuid4(),
                type_id=uuid.uuid4(),
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=uuid.uuid4()),
                db=db,
            )

        facade.update_type.assert_not_awaited()
        db.commit.assert_not_awaited()


class TestValidatorListRoute:
    """GET /api/activity-validators exposes the registered first-party set (AC-1)."""

    def teardown_method(self) -> None:
        from contexts.activities.application.validators import registry

        registry.clear_registry()

    async def test_lists_registered_validators(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators
        from contexts.activities.application.validators import registry

        registry.clear_registry()
        register_first_party_validators()

        out = await activities.list_activity_validators(principal=SimpleNamespace(user_id=uuid.uuid4()))

        assert any(v.id == "exact_match" and v.title == "Exact match" for v in out)

    async def test_empty_when_nothing_registered(self) -> None:
        from contexts.activities.application.validators import registry

        registry.clear_registry()

        out = await activities.list_activity_validators(principal=SimpleNamespace(user_id=uuid.uuid4()))

        assert out == []
