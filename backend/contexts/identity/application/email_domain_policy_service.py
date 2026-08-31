"""Read and replace the email-domain policy singleton (R19a.13).

Replacement is full: the Admin submits the whole policy and the stored row
becomes exactly that. A partial patch over a versioned singleton makes both the
audit trail and the conflict story ambiguous — "what did this change" would have
to be reconstructed from a diff nobody stored.

Two guards, and they are different failures with different recoveries:

* **version** — somebody committed an edit since this form loaded. Reload and
  reapply. Retrying the same body would discard their change.
* **rollout phase** — writes are fenced outside `active`. Nothing about the
  request is wrong and no reload helps; only an operator transition lifts it.

Cache refresh is deliberately *not* part of the write. It has to happen after the
transaction commits, or a rolled-back write would leave every replica reading a
policy that no longer exists, so the caller commits and then calls
:meth:`publish`.
"""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.email_domain_policy_reader import reset_process_cache
from contexts.identity.application.ports import (
    EmailDomainPolicyMirror,
    EmailDomainPolicyRepository,
)
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
    normalise_domain_list,
)
from contexts.identity.domain.errors import (
    EmailDomainPolicyRolloutFenced,
    EmailDomainPolicyUnavailable,
    EmailDomainPolicyVersionMismatch,
)
from shared_kernel import audit


class EmailDomainPolicyService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: EmailDomainPolicyRepository,
        mirror: EmailDomainPolicyMirror,
    ) -> None:
        self._db = db
        self._repository = repository
        self._mirror = mirror

    async def get(self) -> EmailDomainPolicy:
        """The stored policy in any phase.

        Readable while writes are fenced: an operator mid-rollout needs to see
        what is stored precisely because they cannot change it.
        """
        policy = await self._repository.get()
        if policy is None:
            raise EmailDomainPolicyUnavailable("no email-domain policy row exists")
        return policy

    async def replace(
        self,
        *,
        expected_version: int,
        mode: EmailDomainPolicyMode,
        allow: list[str],
        deny: list[str],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> EmailDomainPolicy:
        current = await self.get()
        # Checked before normalisation so a fenced request costs nothing and,
        # more importantly, cannot emit an audit row for a write that will not
        # happen.
        if current.rollout_state is not EmailDomainPolicyRolloutState.ACTIVE:
            raise EmailDomainPolicyRolloutFenced(current.rollout_state.value)

        normalised_allow = normalise_domain_list(allow)
        normalised_deny = normalise_domain_list(deny)

        updated = await self._repository.replace_active(
            expected_version=expected_version,
            mode=mode,
            allow=normalised_allow,
            deny=normalised_deny,
            actor_user_id=actor_user_id,
        )
        if updated is None:
            # The guard covers version *and* phase, so re-read to say which one
            # lost. A freeze can land between the check above and this update.
            latest = await self.get()
            if latest.rollout_state is not EmailDomainPolicyRolloutState.ACTIVE:
                raise EmailDomainPolicyRolloutFenced(latest.rollout_state.value)
            raise EmailDomainPolicyVersionMismatch(
                f"policy is at version {latest.version}, not {expected_version}"
            )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.email_domain_policy_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="email_domain_policy",
                resource_id=None,
                # Counts, never domains. A list of institutional domains
                # identifies the institutions a deployment serves, and the audit
                # log has a wider readership than the Admin surface does.
                metadata={
                    "rollout_state": updated.rollout_state.value,
                    "mode": updated.mode.value,
                    "old_version": current.version,
                    "new_version": updated.version,
                    "allow_count": len(updated.allow),
                    "deny_count": len(updated.deny),
                },
                request_id=request_id,
            ),
        )
        return updated

    async def publish(self, policy: EmailDomainPolicy) -> None:
        """Refresh the acceleration mirror. **Call only after the commit.**

        Best-effort: a failure here leaves the previous mirror value in place
        with its own unextended TTL, so every replica converges on the committed
        policy within 30 seconds. It must never turn a committed write into a
        failed request, which would tell the Admin their change was rolled back
        when it was not.
        """
        try:
            await self._mirror.write(policy)
        except Exception:
            logger.bind(event="email_domain_policy_mirror_publish_failed").warning(
                "email-domain policy committed but the mirror was not refreshed; "
                "replicas converge within the mirror TTL",
                exc_info=True,
            )
        finally:
            # After the mirror write, not before: resetting first leaves a window
            # in which another request in this process reads the *old* mirror
            # value and re-caches it for that value's remaining life. Reset last
            # and the local cache can only be refilled from the new value.
            # In `finally` because a failed mirror write must still drop the
            # snapshot this process is holding of the pre-update policy.
            reset_process_cache()


__all__ = ["EmailDomainPolicyService"]
