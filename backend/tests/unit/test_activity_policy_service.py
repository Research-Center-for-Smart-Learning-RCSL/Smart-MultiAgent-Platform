"""Platform activity governance policy ([R30.29], [R30.30]).

Pins AC-7 (permissive when unset), AC-10 (retention ceiling), AC-11 (an unlocked
field is only a default), and AC-12 (audit + optimistic concurrency). The
enforcement points themselves are covered in `test_activities_services.py`
(authoring) and `test_activity_activation_policy.py` (activation).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application.policy_service import ActivityPolicyService
from contexts.activities.domain.errors import (
    ActivityPolicyVersionMismatch,
    ActivityTypeViolatesPolicy,
)
from contexts.activities.domain.models import PLATFORM_SCOPE, ActivityPolicy, ActivityType, ValidatorKind

_NOW = dt.datetime(2026, 8, 9, tzinfo=dt.UTC)


def _policy(**over: Any) -> ActivityPolicy:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "scope": PLATFORM_SCOPE,
        "expose_payload_to_agent_default": True,
        "expose_payload_to_agent_locked": False,
        "echo_includes_content_default": False,
        "echo_includes_content_locked": False,
        "retention_days_default": None,
        "retention_days_max": None,
        "version": 3,
        "updated_at": _NOW,
        "updated_by_user_id": uuid.uuid4(),
    }
    base.update(over)
    return ActivityPolicy(**base)


def _service(stored: ActivityPolicy | None) -> ActivityPolicyService:
    svc = ActivityPolicyService(MagicMock())
    svc._repo = MagicMock()
    svc._repo.get_platform = AsyncMock(return_value=stored)
    svc._repo.create_platform = AsyncMock()
    svc._repo.update_platform = AsyncMock()
    return svc


class TestEffectivePolicy:
    async def test_permissive_when_no_row_exists(self) -> None:
        """AC-7: installing the capability must change nothing until an admin acts."""
        effective = await _service(None).get_effective()

        assert effective.expose_payload_to_agent_locked is False
        assert effective.echo_includes_content_locked is False
        assert effective.retention_days_max is None
        # version 0 distinguishes "never saved" from a real, deliberately
        # permissive row.
        assert effective.version == 0

    async def test_a_stored_row_wins(self) -> None:
        stored = _policy(expose_payload_to_agent_locked=True)
        assert (await _service(stored).get_effective()).expose_payload_to_agent_locked is True


class TestAssertAllows:
    async def test_permissive_policy_allows_anything(self) -> None:
        svc = _service(None)
        await svc.assert_allows(
            expose_payload_to_agent=True, echo_includes_content=True, retention_days=99_999
        )

    async def test_unlocked_field_is_only_a_default(self) -> None:
        """AC-11: a default pre-fills the form; it does not constrain."""
        svc = _service(_policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=False))

        await svc.assert_allows(
            expose_payload_to_agent=True, echo_includes_content=False, retention_days=None
        )

    async def test_locked_expose_rejects_a_deviation(self) -> None:
        svc = _service(_policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True))

        with pytest.raises(ActivityTypeViolatesPolicy) as exc:
            await svc.assert_allows(
                expose_payload_to_agent=True, echo_includes_content=False, retention_days=None
            )
        # The field name is what lets the UI say which switch is wrong.
        assert exc.value.field == "expose_payload_to_agent"

    async def test_locked_expose_accepts_a_match(self) -> None:
        svc = _service(_policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True))

        await svc.assert_allows(
            expose_payload_to_agent=False, echo_includes_content=False, retention_days=None
        )

    async def test_locked_echo_rejects_a_deviation(self) -> None:
        svc = _service(_policy(echo_includes_content_default=False, echo_includes_content_locked=True))

        with pytest.raises(ActivityTypeViolatesPolicy) as exc:
            await svc.assert_allows(
                expose_payload_to_agent=True, echo_includes_content=True, retention_days=None
            )
        assert exc.value.field == "echo_includes_content"

    @pytest.mark.parametrize(
        ("retention", "allowed"),
        [(365, True), (364, True), (366, False), (None, True)],
        ids=["at-the-cap", "under", "over", "unset"],
    )
    async def test_retention_ceiling(self, retention: int | None, allowed: bool) -> None:
        """AC-10. An unset retention is never a breach: the cap bounds how long
        data may be kept, not how short."""
        svc = _service(_policy(retention_days_max=365))

        if allowed:
            await svc.assert_allows(
                expose_payload_to_agent=True, echo_includes_content=False, retention_days=retention
            )
        else:
            with pytest.raises(ActivityTypeViolatesPolicy) as exc:
                await svc.assert_allows(
                    expose_payload_to_agent=True,
                    echo_includes_content=False,
                    retention_days=retention,
                )
            assert exc.value.field == "retention_days"

    async def test_a_supplied_policy_avoids_a_second_read(self) -> None:
        svc = _service(_policy())
        await svc.assert_allows(
            expose_payload_to_agent=True,
            echo_includes_content=False,
            retention_days=None,
            policy=_policy(),
        )
        svc._repo.get_platform.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_assert_type_allowed_reads_the_stored_type(self) -> None:
        """AC-9's mechanism: the *stored* values are what get checked."""
        svc = _service(_policy(expose_payload_to_agent_default=False, expose_payload_to_agent_locked=True))
        at = ActivityType(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            key="k",
            name="n",
            payload_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            validator_kind=ValidatorKind.IN_PROCESS,
            validator_config={"validator_id": "filled_count", "min_filled": 0},
            retention_days=None,
            version=1,
            created_at=_NOW,
            expose_payload_to_agent=True,
        )

        with pytest.raises(ActivityTypeViolatesPolicy):
            await svc.assert_type_allowed(at)


class TestUpdate:
    async def test_first_write_creates_without_a_version(self) -> None:
        svc = _service(None)
        created = _policy(version=1)
        svc._repo.create_platform = AsyncMock(return_value=created)  # type: ignore[attr-defined]

        with patch("contexts.activities.application.policy_service.audit.emit", new=AsyncMock()) as emit:
            result = await svc.update(
                expose_payload_to_agent_default=True,
                expose_payload_to_agent_locked=False,
                echo_includes_content_default=False,
                echo_includes_content_locked=False,
                retention_days_default=None,
                retention_days_max=None,
                expected_version=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        assert result is created
        event = emit.await_args.args[1]
        assert event.action == "activity_policy.updated"
        # Nothing preceded it, so `previous` is empty rather than fabricated.
        assert event.metadata["previous"] == {}

    async def test_replacing_without_a_version_is_rejected(self) -> None:
        svc = _service(_policy())

        with pytest.raises(ActivityPolicyVersionMismatch):
            await svc.update(
                expose_payload_to_agent_default=True,
                expose_payload_to_agent_locked=True,
                echo_includes_content_default=False,
                echo_includes_content_locked=False,
                retention_days_default=None,
                retention_days_max=None,
                expected_version=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_a_stale_version_is_rejected(self) -> None:
        """AC-12: someone else changed the policy since this form was loaded."""
        svc = _service(_policy(version=5))
        svc._repo.update_platform = AsyncMock(return_value=None)  # type: ignore[attr-defined]

        with pytest.raises(ActivityPolicyVersionMismatch):
            await svc.update(
                expose_payload_to_agent_default=True,
                expose_payload_to_agent_locked=True,
                echo_includes_content_default=False,
                echo_includes_content_locked=False,
                retention_days_default=None,
                retention_days_max=None,
                expected_version=4,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_audit_carries_previous_and_new_values(self) -> None:
        """AC-12. A policy change can push every project's participant text into
        agent prompts, so the log alone must answer "what did it used to be"."""
        before = _policy(expose_payload_to_agent_locked=False, version=5)
        after = _policy(expose_payload_to_agent_locked=True, version=6)
        svc = _service(before)
        svc._repo.update_platform = AsyncMock(return_value=after)  # type: ignore[attr-defined]

        with patch("contexts.activities.application.policy_service.audit.emit", new=AsyncMock()) as emit:
            await svc.update(
                expose_payload_to_agent_default=True,
                expose_payload_to_agent_locked=True,
                echo_includes_content_default=False,
                echo_includes_content_locked=False,
                retention_days_default=None,
                retention_days_max=None,
                expected_version=5,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        meta = emit.await_args.args[1].metadata
        assert meta["previous"]["expose_payload_to_agent_locked"] == "False"
        assert meta["new"]["expose_payload_to_agent_locked"] == "True"
        assert meta["previous"]["version"] == "5"
        assert meta["new"]["version"] == "6"
