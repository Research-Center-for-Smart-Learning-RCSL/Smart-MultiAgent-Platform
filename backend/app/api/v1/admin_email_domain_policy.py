"""`/api/admin/email-domain-policy` — the R19a.13 policy an operator sets.

Before this route existed the policy was set with `redis-cli` and
`docs/operations.md` §7a.5 carried the recipe, which is why it could be lost to
an eviction with nobody noticing. It is a platform-Admin surface and exposes no
org or project data, so `require_admin` is the whole access control.

Everything below goes through `IdentityFacade`; nothing here touches a service,
repository or table.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.identity.domain.email_domain_policy import (
    MAX_DOMAIN_LENGTH,
    MAX_DOMAINS_PER_LIST,
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import EmailDomainPolicyVersionMismatch
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])

#: Each entry is bounded as well as the list. 253 octets is the DNS limit, so a
#: longer string cannot be a domain and need not reach normalisation; the list
#: bound stops an unbounded array from being parsed at all.
#:
#: Input only. The response deliberately reuses neither bound: the boot-time
#: legacy import applies no count cap, so a deployment that had more than
#: `MAX_DOMAINS_PER_LIST` domains in Redis holds a row this cap cannot describe,
#: and validating the *response* against it would turn the one screen an operator
#: needs in order to shrink that list into a 500.
_Domain = Annotated[str, StringConstraints(max_length=MAX_DOMAIN_LENGTH)]
_DomainListIn = Annotated[list[_Domain], Field(max_length=MAX_DOMAINS_PER_LIST)]


class EmailDomainPolicyOut(BaseModel):
    """The stored policy plus the rollout facts the Admin UI needs.

    ``rollout_state`` is not decoration: the form is read-only outside `active`,
    and without it the UI could only discover that by attempting a write and
    reading a 409. ``legacy_mirrored_version`` is the rollback marker — equal to
    ``version`` means the legacy triple has been written and read back, which is
    the documented precondition for starting an old image.
    """

    mode: EmailDomainPolicyMode
    allow: list[str]
    deny: list[str]
    version: int
    rollout_state: EmailDomainPolicyRolloutState
    legacy_mirrored_version: int | None
    updated_at: str | None
    editable: bool


class EmailDomainPolicyIn(BaseModel):
    """A full replacement, not a patch.

    Bounds are declared here as well as in the domain normaliser so an oversized
    body is rejected at the boundary rather than after being parsed and
    normalised — the normaliser is the decision, this is the cost control.
    """

    mode: EmailDomainPolicyMode
    allow: _DomainListIn = Field(default_factory=list)
    deny: _DomainListIn = Field(default_factory=list)


def _policy_out(policy: EmailDomainPolicy) -> EmailDomainPolicyOut:
    return EmailDomainPolicyOut(
        mode=policy.mode,
        allow=sorted(policy.allow),
        deny=sorted(policy.deny),
        version=policy.version,
        rollout_state=policy.rollout_state,
        legacy_mirrored_version=policy.legacy_mirrored_version,
        updated_at=policy.updated_at.isoformat() if policy.updated_at else None,
        editable=policy.rollout_state is EmailDomainPolicyRolloutState.ACTIVE,
    )


@router.get("/email-domain-policy")
async def get_email_domain_policy(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> EmailDomainPolicyOut:
    """The policy in force, readable in every rollout phase.

    Readable while writes are fenced on purpose: an operator mid-rollout needs
    to see what is stored precisely because they cannot change it.
    """
    return _policy_out(await IdentityFacade(db).get_email_domain_policy())


@router.put("/email-domain-policy")
async def put_email_domain_policy(
    body: EmailDomainPolicyIn,
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> EmailDomainPolicyOut:
    """Replace the policy. Permitted only while the rollout state is `active`.

    ``If-Match`` carries the version the Admin's form was built against and is
    required: the row always exists by the time this route is reachable (the
    startup import creates it), so there is no "first write" case that could
    legitimately omit it. A missing or unparseable precondition is a mismatch
    rather than "no precondition" — treating an unreadable header as permission
    to overwrite would defeat the point of having one.
    """
    if if_match is None:
        raise EmailDomainPolicyVersionMismatch("If-Match is required")
    try:
        expected_version = int(if_match.strip().strip('"'))
    except ValueError:
        raise EmailDomainPolicyVersionMismatch(f"If-Match is not a version: {if_match!r}") from None

    facade = IdentityFacade(db)
    policy = await facade.update_email_domain_policy(
        expected_version=expected_version,
        mode=body.mode,
        allow=body.allow,
        deny=body.deny,
        actor_user_id=admin.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    # After the commit, never before: refreshing the mirror inside the
    # transaction would publish a policy a rollback could still erase.
    await facade.publish_email_domain_policy(policy)
    return _policy_out(policy)


__all__ = ["router"]
