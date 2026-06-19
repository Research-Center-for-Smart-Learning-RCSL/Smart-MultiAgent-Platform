"""Unified invite flow (§22.2a, R6.09, R6.10, R6.11 guest-wrapping).

The invite state machine:

    pending ──accept──► accepted  (membership row inserted)
            ──reject──► rejected
            ──revoke──► revoked   (inviter or admin)
            ──expire──► expired   (nightly worker)

Invite token is hashed in `invites.token_hash`; the plaintext is emailed and
never persisted. Acceptance requires the caller be logged in AND verified
(R6.11 — unverified accounts cannot accept Guest/Org/Project invites).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.interfaces.facade import IdentityFacade
from contexts.notification.interfaces.facade import NotificationFacade, NotificationKind
from contexts.tenancy.domain.errors import (
    InviteExpired,
    InviteNotForCaller,
    InviteNotFound,
)
from contexts.tenancy.domain.models import (
    Invite,
    InviteScope,
    OrgMemberRole,
    ProjectMemberRole,
)
from contexts.tenancy.domain.models import (
    InviteState as InviteState,
)
from contexts.tenancy.infrastructure import tables as _t
from contexts.tenancy.infrastructure.repositories import (
    InviteRepository,
    OrgMemberRepository,
    ProjectMemberRepository,
)
from shared_kernel import audit
from shared_kernel.auth import ratelimit
from shared_kernel.auth.clients import now


@dataclass(frozen=True, slots=True)
class InviteCreated:
    invite: Invite
    plaintext_token: str


def _default_public_origin() -> str:
    # Single origin (§19a.07); mirrors app.api.v1.auth._public_origin.
    origins = get_settings().security.cors_origins
    return (origins[0] if origins else "http://localhost:8080").rstrip("/")


def _recipient_digest(addr: str) -> str:
    """Proxy to IdentityFacade.recipient_digest (avoids cross-context import)."""
    return IdentityFacade.recipient_digest(addr)


class InviteService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        email_sender: Any = None,
        public_origin: str | None = None,
    ) -> None:
        self._db = db
        self._invites = InviteRepository(db)
        self._org_members = OrgMemberRepository(db)
        self._project_members = ProjectMemberRepository(db)
        self._identity = IdentityFacade(db)
        # Legacy: tests may inject a custom sender (duck-typed ``EmailSender``
        # protocol). When provided, ``_email_invite`` falls back to a direct
        # template-render + send path using lazy imports.
        self._custom_emailer = email_sender
        self._public_origin = (public_origin or _default_public_origin()).rstrip("/")

    async def create_org_invite(
        self,
        *,
        org_id: uuid.UUID,
        inviter_user_id: uuid.UUID,
        invitee_email: str,
        role: OrgMemberRole = OrgMemberRole.MEMBER,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> InviteCreated:
        token, invite = await self._invites.create(
            scope_type=InviteScope.ORG,
            scope_id=org_id,
            role=role.value,
            inviter_user_id=inviter_user_id,
            invitee_email=invitee_email,
            invitee_user_id=None,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.member_invited",
                actor_user_id=inviter_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org_id,
                metadata={"invitee_digest": _recipient_digest(invitee_email), "role": role.value},
                request_id=request_id,
            ),
        )
        await self._notify_invitee(invitee_email, invite.id, InviteScope.ORG, org_id)
        await self._email_invite(invitee_email, token, InviteScope.ORG, org_id)
        return InviteCreated(invite=invite, plaintext_token=token)

    async def create_project_invite(
        self,
        *,
        project_id: uuid.UUID,
        inviter_user_id: uuid.UUID,
        invitee_email: str,
        role: ProjectMemberRole = ProjectMemberRole.MEMBER,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> InviteCreated:
        token, invite = await self._invites.create(
            scope_type=InviteScope.PROJECT,
            scope_id=project_id,
            role=role.value,
            inviter_user_id=inviter_user_id,
            invitee_email=invitee_email,
            invitee_user_id=None,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_invited",
                actor_user_id=inviter_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                metadata={"invitee_digest": _recipient_digest(invitee_email), "role": role.value},
                request_id=request_id,
            ),
        )
        await self._notify_invitee(invitee_email, invite.id, InviteScope.PROJECT, project_id)
        await self._email_invite(invitee_email, token, InviteScope.PROJECT, project_id)
        return InviteCreated(invite=invite, plaintext_token=token)

    async def _notify_invitee(
        self,
        invitee_email: str,
        invite_id: uuid.UUID,
        scope: InviteScope,
        scope_id: uuid.UUID,
    ) -> None:
        # Look up invitee by email without crossing into the identity context's
        # repository layer — raw query is intentional here.
        row = (
            await self._db.execute(
                sa.text(
                    "SELECT id FROM users"
                    " WHERE LOWER(email) = LOWER(:email)"
                    " AND deleted_at IS NULL"
                ).bindparams(email=invitee_email)
            )
        ).first()
        if row is None:
            return
        scope_label = "org" if scope is InviteScope.ORG else "project"
        await NotificationFacade(self._db).send(
            user_id=row.id,
            kind=NotificationKind.INVITE_RECEIVED,
            title=f"You have been invited to a {scope_label}",
            metadata={"invite_id": str(invite_id), "scope": scope_label, "scope_id": str(scope_id)},
        )

    async def _scope_name(self, scope: InviteScope, scope_id: uuid.UUID) -> str:
        table = _t.orgs if scope is InviteScope.ORG else _t.projects
        row = (await self._db.execute(sa.select(table.c.name).where(table.c.id == scope_id))).first()
        return row.name if row else ""

    async def _email_invite(
        self,
        invitee_email: str,
        token: str,
        scope: InviteScope,
        scope_id: uuid.UUID,
    ) -> None:
        # R6.09: the invite mail carries the plaintext token in the URL fragment
        # (`#token=`, SEC-8). The SPA accept route reads it from `location.hash`
        # and POSTs to `/api/invites/accept-by-token`; an unregistered invitee is
        # routed through sign-up first, then auto-enrolled. This is what makes the
        # previously write-only token column actually do something.
        # Per-recipient rate limit (mirrors auth_service register/reset): without
        # it this is an unauthenticated mailbomb — an inviter could spam a
        # victim's inbox by re-POSTing invite-create with their address. Cap at
        # 5 / 10 min; over the limit we SKIP the mail (the invite row is already
        # created + audited) rather than failing invite creation.
        digest = _recipient_digest(invitee_email)
        rl_key = "rl:inv:e:" + hashlib.sha256(invitee_email.lower().encode()).hexdigest()[:24]
        rl = await ratelimit.check_raw(key=rl_key, window_sec=600, max_count=5)
        if not rl.allowed:
            logger.bind(event="invite_email_skipped_ratelimit", recipient=digest).info(
                "invite email suppressed: per-recipient rate limit exceeded"
            )
            return

        scope_label = "org" if scope is InviteScope.ORG else "project"
        scope_name = await self._scope_name(scope, scope_id)
        if self._custom_emailer is not None:
            # Test-injected sender — render + send directly via lazy imports.
            from contexts.identity.infrastructure import email_templates
            from contexts.identity.infrastructure.email import EmailMessage

            accept_link = f"{self._public_origin}/invites/accept#token={token}"
            rendered = email_templates.invite(
                scope_label=scope_label, scope_name=scope_name, accept_link=accept_link,
            )
            await self._custom_emailer.send(
                EmailMessage(
                    to=invitee_email,
                    subject=rendered.subject,
                    text_body=rendered.text_body,
                    html_body=rendered.html_body,
                    template="invite",
                )
            )
        else:
            # Production path — delegate to IdentityFacade.
            await self._identity.send_invite_email(
                to_email=invitee_email,
                scope_label=scope_label,
                scope_name=scope_name,
                invite_token=token,
                base_url=self._public_origin,
            )
        # email.sent audit with template + recipient digest (never plaintext).
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="email.sent",
                resource_type=scope_label,
                resource_id=scope_id,
                metadata={"template": "invite", "recipient": digest},
            ),
        )

    async def list_inbound(
        self,
        *,
        caller_email: str,
        caller_user_id: uuid.UUID,
        states: Sequence[InviteState] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Invite]:
        return await self._invites.list_for_user(
            email=caller_email,
            user_id=caller_user_id,
            states=states,
            limit=limit,
            offset=offset,
        )

    async def scope_names(self, invites: Sequence[Invite]) -> dict[tuple[str, uuid.UUID], str]:
        """Batch-fetch org/project display names for a list of invites."""
        import sqlalchemy as sa  # — kept local to avoid circular at module load

        names: dict[tuple[str, uuid.UUID], str] = {}
        org_ids = [i.scope_id for i in invites if i.scope_type is InviteScope.ORG]
        proj_ids = [i.scope_id for i in invites if i.scope_type is InviteScope.PROJECT]
        if org_ids:
            rows = (
                await self._db.execute(
                    sa.select(_t.orgs.c.id, _t.orgs.c.name).where(_t.orgs.c.id.in_(org_ids))
                )
            ).all()
            for row in rows:
                names[("org", row.id)] = row.name
        if proj_ids:
            rows = (
                await self._db.execute(
                    sa.select(_t.projects.c.id, _t.projects.c.name).where(_t.projects.c.id.in_(proj_ids))
                )
            ).all()
            for row in rows:
                names[("project", row.id)] = row.name
        return names

    async def accept(
        self,
        *,
        invite_id: uuid.UUID,
        caller_email: str,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Invite:
        invite = await self._invites.get(invite_id)
        if invite is None:
            raise InviteNotFound(str(invite_id))
        if invite.invitee_email.lower() != caller_email.lower():
            raise InviteNotForCaller(str(invite_id))
        return await self._finalize_acceptance(
            invite,
            caller_user_id=caller_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def accept_by_token(
        self,
        *,
        token: str,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Invite:
        """Accept an invite by its emailed token link (R6.09).

        Possession of the token is the authorisation here — it replaces the
        logged-in-email match of :meth:`accept`, so an invitee who signed up
        with a different address (or via the link before their email was known)
        can still redeem it. The caller must still be logged in AND verified;
        that gate is enforced at the router (R6.11).
        """
        invite = await self._invites.get_by_token(token)
        if invite is None:
            raise InviteNotFound("invite token invalid")
        return await self._finalize_acceptance(
            invite,
            caller_user_id=caller_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def _finalize_acceptance(
        self,
        invite: Invite,
        *,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> Invite:
        invite_id = invite.id
        if invite.state is not InviteState.PENDING:
            raise InviteNotFound(str(invite_id))
        if invite.expires_at < now():
            await self._invites.transition(
                invite_id=invite_id,
                new_state=InviteState.EXPIRED,
            )
            raise InviteExpired(str(invite_id))

        updated = await self._invites.transition(
            invite_id=invite_id,
            new_state=InviteState.ACCEPTED,
            invitee_user_id=caller_user_id,
        )
        if updated is None:
            raise InviteNotFound(str(invite_id))
        # Guard: refuse if the target org/project has been soft-deleted.
        scope_table = _t.orgs if invite.scope_type is InviteScope.ORG else _t.projects
        scope_result = await self._db.execute(
            scope_table.select().where(
                scope_table.c.id == invite.scope_id,
                scope_table.c.deleted_at.is_(None),
            )
        )
        if scope_result.first() is None:
            raise InviteNotFound(str(invite_id))

        # Create the actual membership row in the correct scope.
        if invite.scope_type is InviteScope.ORG:
            await self._org_members.add(
                org_id=invite.scope_id,
                user_id=caller_user_id,
                role=OrgMemberRole(invite.role),
                is_original_creator=False,
            )
            action = "org.member_added"
        else:
            await self._project_members.add(
                project_id=invite.scope_id,
                user_id=caller_user_id,
                role=ProjectMemberRole(invite.role),
            )
            action = "project.member_added"
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type=invite.scope_type.value,
                resource_id=invite.scope_id,
                metadata={"via_invite": str(invite_id)},
                request_id=request_id,
            ),
        )
        return updated

    async def reject(
        self,
        *,
        invite_id: uuid.UUID,
        caller_email: str,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Invite:
        invite = await self._invites.get(invite_id)
        if invite is None:
            raise InviteNotFound(str(invite_id))
        if invite.invitee_email.lower() != caller_email.lower():
            raise InviteNotForCaller(str(invite_id))
        if invite.state is not InviteState.PENDING:
            raise InviteNotFound(str(invite_id))
        updated = await self._invites.transition(
            invite_id=invite_id,
            new_state=InviteState.REJECTED,
        )
        if updated is None:
            raise InviteNotFound(str(invite_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=(
                    "org.member_invite_rejected"
                    if invite.scope_type is InviteScope.ORG
                    else "project.member_invite_rejected"
                ),
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type=invite.scope_type.value,
                resource_id=invite.scope_id,
                metadata={"invite_id": str(invite_id)},
                request_id=request_id,
            ),
        )
        return updated


__all__ = ["InviteCreated", "InviteService"]
