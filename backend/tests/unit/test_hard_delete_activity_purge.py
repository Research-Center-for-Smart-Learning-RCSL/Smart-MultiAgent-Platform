"""Hard delete erases a user's activity rows explicitly -- AC-5.

Spec: ``docs/tasks/2026-10-05-guest-identity-foreign-keys/spec.md`` §7.3.

0098 dropped the ``users`` cascades on ``activity_sessions.subject_user_id`` and
``activity_submissions.producer_user_id`` so guests can write them. Without an
explicit purge, hard-deleting a user would silently stop erasing their activity
history. The db-tier test pins what the purge deletes; this pins that the admin
path runs it, and before the ``users`` row goes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_hard_delete_purges_activity_rows_before_deleting_the_user() -> None:
    from contexts.identity.application.admin_service import AdminService

    target = uuid.uuid4()
    order: list[str] = []

    db = AsyncMock()

    async def _execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        if "DELETE FROM users" in str(statement):
            order.append("users")
        return MagicMock()

    db.execute.side_effect = _execute

    svc = AdminService(db, email_domain_policy=AsyncMock())
    tenancy = AsyncMock()
    tenancy.orgs_blocking_self_delete.return_value = []
    tenancy.prepare_hard_delete.return_value = set()
    activities = MagicMock()

    async def _purge(user_id: uuid.UUID) -> tuple[int, int]:
        assert user_id == target
        order.append("activities")
        return 1, 1

    activities.purge_user_activity_rows.side_effect = _purge

    with (
        patch.object(
            svc._users,
            "get_by_id",
            new=AsyncMock(return_value=MagicMock(deleted_at=datetime(2020, 1, 1, tzinfo=UTC))),
        ),
        patch("contexts.tenancy.interfaces.facade.TenancyFacade", return_value=tenancy),
        patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=activities),
        patch("contexts.identity.application.admin_service.audit.emit", new_callable=AsyncMock),
    ):
        await svc.hard_delete_user(target_user_id=target, admin_user_id=uuid.uuid4(), actor_ip=None)

    assert order == ["activities", "users"]
