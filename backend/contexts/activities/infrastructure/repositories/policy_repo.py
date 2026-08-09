"""Persistence for the platform activity governance policy ([R30.29]).

Caller owns commit, matching the other repositories in this context.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import ActivityPolicyVersionMismatch
from contexts.activities.domain.models import PLATFORM_SCOPE, ActivityPolicy
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount

_POLICY_COLS = (
    t.activity_policies.c.id,
    t.activity_policies.c.scope,
    t.activity_policies.c.expose_payload_to_agent_default,
    t.activity_policies.c.expose_payload_to_agent_locked,
    t.activity_policies.c.echo_includes_content_default,
    t.activity_policies.c.echo_includes_content_locked,
    t.activity_policies.c.retention_days_default,
    t.activity_policies.c.retention_days_max,
    t.activity_policies.c.version,
    t.activity_policies.c.updated_at,
    t.activity_policies.c.updated_by_user_id,
)

_SETTABLE = (
    "expose_payload_to_agent_default",
    "expose_payload_to_agent_locked",
    "echo_includes_content_default",
    "echo_includes_content_locked",
    "retention_days_default",
    "retention_days_max",
)


def _row_to_policy(row: object) -> ActivityPolicy:
    return ActivityPolicy(
        id=row.id,  # type: ignore[attr-defined]
        scope=row.scope,  # type: ignore[attr-defined]
        expose_payload_to_agent_default=row.expose_payload_to_agent_default,  # type: ignore[attr-defined]
        expose_payload_to_agent_locked=row.expose_payload_to_agent_locked,  # type: ignore[attr-defined]
        echo_includes_content_default=row.echo_includes_content_default,  # type: ignore[attr-defined]
        echo_includes_content_locked=row.echo_includes_content_locked,  # type: ignore[attr-defined]
        retention_days_default=row.retention_days_default,  # type: ignore[attr-defined]
        retention_days_max=row.retention_days_max,  # type: ignore[attr-defined]
        version=row.version,  # type: ignore[attr-defined]
        updated_at=row.updated_at,  # type: ignore[attr-defined]
        updated_by_user_id=row.updated_by_user_id,  # type: ignore[attr-defined]
    )


class ActivityPolicyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_platform(self) -> ActivityPolicy | None:
        """The single platform row, or None when no admin has saved one yet."""
        row = (
            await self._db.execute(
                sa.select(*_POLICY_COLS).where(t.activity_policies.c.scope == PLATFORM_SCOPE)
            )
        ).first()
        return _row_to_policy(row) if row is not None else None

    async def create_platform(self, *, values: dict[str, Any], actor_user_id: uuid.UUID) -> ActivityPolicy:
        """Insert the first platform policy.

        Two admins saving the first policy concurrently both take this path (there
        is no version to match against yet), and the second loses to
        ``uq_activity_policies_platform``. That is the same conflict the update
        path reports as a 409, so it is translated rather than allowed to escape as
        an unhandled IntegrityError and a 500.
        """
        try:
            row = (
                await self._db.execute(
                    t.activity_policies.insert()
                    .values(
                        scope=PLATFORM_SCOPE,
                        updated_by_user_id=actor_user_id,
                        **{k: values[k] for k in _SETTABLE},
                    )
                    .returning(*_POLICY_COLS)
                )
            ).first()
        except IntegrityError as exc:
            if "uq_activity_policies_platform" in str(exc.orig or exc).lower():
                raise ActivityPolicyVersionMismatch(
                    "another administrator created the policy first; reload and retry"
                ) from exc
            raise
        if row is None:  # pragma: no cover - RETURNING on a successful insert
            raise RuntimeError("insert returned no row")
        return _row_to_policy(row)

    async def update_platform(
        self, *, expected_version: int, values: dict[str, Any], actor_user_id: uuid.UUID
    ) -> ActivityPolicy | None:
        """Bump the row only if it still carries ``expected_version``.

        Returns None when the guard did not match, which the service maps to a
        409 — the admin's form was built against a policy someone has since
        changed, and blind-overwriting would silently discard their edit.
        """
        result = await self._db.execute(
            t.activity_policies.update()
            .where(
                sa.and_(
                    t.activity_policies.c.scope == PLATFORM_SCOPE,
                    t.activity_policies.c.version == expected_version,
                )
            )
            .values(
                version=t.activity_policies.c.version + 1,
                updated_at=now(),
                updated_by_user_id=actor_user_id,
                **{k: values[k] for k in _SETTABLE},
            )
            .returning(*_POLICY_COLS)
        )
        row = result.first()
        if row is None:
            return None
        # Defensive: RETURNING with a guarded WHERE can only produce the one row.
        if rowcount(result) > 1:  # pragma: no cover - scope is uniquely indexed
            raise RuntimeError("policy update matched more than one row")
        return _row_to_policy(row)


__all__ = ["ActivityPolicyRepository"]
