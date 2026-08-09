"""Platform activity governance policy: read, write, and enforcement ([R30.29], [R30.30]).

Two responsibilities that belong together because they share one rule set: serving
the effective policy, and deciding whether a given set of governance-field values
satisfies it. The second is called from two places — type registration/edit and
room activation — which is what makes a tightened policy reach types that already
exist without the platform ever rewriting a stored row.

Never commits; the route's ``db_session`` owns the transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import (
    ActivityPolicyInconsistent,
    ActivityPolicyVersionMismatch,
    ActivityTypeViolatesPolicy,
)
from contexts.activities.domain.models import (
    PERMISSIVE_POLICY,
    ActivityPolicy,
    ActivityType,
)
from contexts.activities.infrastructure.repositories.policy_repo import ActivityPolicyRepository
from shared_kernel import audit

_EXPOSE = "expose_payload_to_agent"
_ECHO = "echo_includes_content"
_RETENTION = "retention_days"


class ActivityPolicyService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ActivityPolicyRepository(db)

    async def get_effective(self) -> ActivityPolicy:
        """The policy in force. Permissive when no admin has ever saved one.

        Falling back rather than seeding a row at boot is deliberate: an unsaved
        policy and a policy an admin deliberately set to permissive are then
        distinguishable (``version == 0`` vs a real row), and installing the
        capability writes nothing.
        """
        return await self._repo.get_platform() or PERMISSIVE_POLICY

    async def assert_allows(
        self,
        *,
        expose_payload_to_agent: bool,
        echo_includes_content: bool,
        retention_days: int | None,
        policy: ActivityPolicy | None = None,
    ) -> None:
        """Raise ``ActivityTypeViolatesPolicy`` if these values breach the policy.

        ``policy`` may be passed in by a caller that already read it, so a single
        request does not read the row twice.
        """
        effective = policy if policy is not None else await self.get_effective()

        if (
            effective.expose_payload_to_agent_locked
            and expose_payload_to_agent != effective.expose_payload_to_agent_default
        ):
            raise ActivityTypeViolatesPolicy(
                _EXPOSE,
                f"platform policy requires {_EXPOSE} to be "
                f"{str(effective.expose_payload_to_agent_default).lower()}",
            )

        if (
            effective.echo_includes_content_locked
            and echo_includes_content != effective.echo_includes_content_default
        ):
            raise ActivityTypeViolatesPolicy(
                _ECHO,
                f"platform policy requires {_ECHO} to be "
                f"{str(effective.echo_includes_content_default).lower()}",
            )

        # An unset retention (follow the room's purge) is never a breach: the
        # ceiling bounds how long data may be kept, not how short.
        if (
            effective.retention_days_max is not None
            and retention_days is not None
            and retention_days > effective.retention_days_max
        ):
            raise ActivityTypeViolatesPolicy(
                _RETENTION,
                f"platform policy caps {_RETENTION} at {effective.retention_days_max}",
            )

    async def assert_type_allowed(self, activity_type: ActivityType) -> None:
        """The activation-time gate ([R30.30]).

        Checks a *stored* type against the current policy, which is how tightening
        reaches types registered before the change without rewriting them.
        """
        await self.assert_allows(
            expose_payload_to_agent=activity_type.expose_payload_to_agent,
            echo_includes_content=activity_type.echo_includes_content,
            retention_days=activity_type.retention_days,
        )

    async def update(
        self,
        *,
        expose_payload_to_agent_default: bool,
        expose_payload_to_agent_locked: bool,
        echo_includes_content_default: bool,
        echo_includes_content_locked: bool,
        retention_days_default: int | None,
        retention_days_max: int | None,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivityPolicy:
        """Create or replace the platform policy under optimistic concurrency.

        ``expected_version`` comes from ``If-Match``. It is required when a row
        already exists and ignored on first write, mirroring
        ``prompt_studio``'s ``put_config``.
        """
        # A policy must not contradict itself. Pydantic and the table CHECKs each
        # validate the two retention fields independently, so `default=500,
        # max=100` passes both — and then the authoring form pre-fills 500 and the
        # owner's first save is rejected by the very policy that supplied it.
        if (
            retention_days_default is not None
            and retention_days_max is not None
            and retention_days_default > retention_days_max
        ):
            raise ActivityPolicyInconsistent(
                f"retention default {retention_days_default} exceeds the maximum {retention_days_max}"
            )

        values = {
            "expose_payload_to_agent_default": expose_payload_to_agent_default,
            "expose_payload_to_agent_locked": expose_payload_to_agent_locked,
            "echo_includes_content_default": echo_includes_content_default,
            "echo_includes_content_locked": echo_includes_content_locked,
            "retention_days_default": retention_days_default,
            "retention_days_max": retention_days_max,
        }

        existing = await self._repo.get_platform()
        if existing is None:
            saved = await self._repo.create_platform(values=values, actor_user_id=actor_user_id)
            previous: dict[str, str] = {}
        else:
            if expected_version is None:
                raise ActivityPolicyVersionMismatch("If-Match is required to replace an existing policy")
            updated = await self._repo.update_platform(
                expected_version=expected_version, values=values, actor_user_id=actor_user_id
            )
            if updated is None:
                raise ActivityPolicyVersionMismatch(f"policy version is no longer {expected_version}")
            saved = updated
            previous = _audit_values(existing)

        # Previous *and* new values: a policy change is the platform-wide switch
        # that can push every project's participant text into agent prompts, so
        # "what did it used to be" has to be recoverable from the log alone.
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="activity_policy.updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_policy",
                resource_id=saved.id,
                metadata={"previous": previous, "new": _audit_values(saved)},
                request_id=request_id,
            ),
        )
        return saved


def _audit_values(policy: ActivityPolicy) -> dict[str, str]:
    return {
        "expose_payload_to_agent_default": str(policy.expose_payload_to_agent_default),
        "expose_payload_to_agent_locked": str(policy.expose_payload_to_agent_locked),
        "echo_includes_content_default": str(policy.echo_includes_content_default),
        "echo_includes_content_locked": str(policy.echo_includes_content_locked),
        "retention_days_default": str(policy.retention_days_default),
        "retention_days_max": str(policy.retention_days_max),
        "version": str(policy.version),
    }


__all__ = ["ActivityPolicyService"]
