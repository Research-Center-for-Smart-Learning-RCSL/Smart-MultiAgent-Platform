"""`/api/orgs/*` — organisation CRUD + membership + OC transfer (§22.2)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.application.invite_service import InviteService
from contexts.tenancy.application.oc_transfer_service import OCTransferService

# `OrgMemberRole` is re-exported by org_service so routers can import role
# enums without reaching into `contexts.tenancy.domain` (import-linter rule 3).
from contexts.tenancy.application.org_service import OrgMemberRole, OrgService
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/orgs", tags=["orgs"])


# ---------- Schemas --------------------------------------------------------


class OrgQuotasOut(BaseModel):
    users: int
    projects: int
    chatrooms: int
    agents: int
    workflows: int
    computed_at: str | None
    advisory_targets: dict[str, int]


class OrgCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    creator_user_id: uuid.UUID
    version: int
    created_at: str
    deleted_at: str | None
    default_project_id: uuid.UUID | None = None


class OrgMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    is_original_creator: bool
    joined_at: str


class InviteOut(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    invitee_email: str
    role: str
    state: str
    expires_at: str


class MemberPatchIn(BaseModel):
    role: str = Field(pattern="^(owner|member)$")


class InviteCreateIn(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(owner|member)$")


class TransferInitIn(BaseModel):
    target_user_id: uuid.UUID


class TransferOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    initiator_user_id: uuid.UUID
    target_user_id: uuid.UUID
    state: str
    expires_at: str


# ---------- Routes --------------------------------------------------------


@router.get("")
async def list_orgs(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[OrgOut]:
    service = OrgService(db)
    orgs = await service.list_for_user(principal.user_id)
    return [
        OrgOut(
            id=o.id,
            name=o.name,
            creator_user_id=o.creator_user_id,
            version=o.version,
            created_at=o.created_at.isoformat(),
            deleted_at=o.deleted_at.isoformat() if o.deleted_at else None,
        )
        for o in orgs
    ]


@router.get("/{org_id}/quotas")
async def get_org_quotas(
    org_id: uuid.UUID = Path(...),
    _=Depends(require_membership(org_param="org_id")),
) -> OrgQuotasOut:
    from app.workers.tasks.advisory import get_advisory_targets, get_org_advisory

    snapshot = await get_org_advisory(org_id)
    targets = await get_advisory_targets()
    if snapshot is None:
        return OrgQuotasOut(
            users=0,
            projects=0,
            chatrooms=0,
            agents=0,
            workflows=0,
            computed_at=None,
            advisory_targets=targets,
        )
    return OrgQuotasOut(
        users=snapshot.get("users", 0),
        projects=snapshot.get("projects", 0),
        chatrooms=snapshot.get("chatrooms", 0),
        agents=snapshot.get("agents", 0),
        workflows=snapshot.get("workflows", 0),
        computed_at=snapshot.get("computed_at"),
        advisory_targets=targets,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreateIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_CREATE, scope_from_path())),
    db: AsyncSession = Depends(db_session),
) -> OrgOut:
    service = OrgService(db)
    created = await service.create(
        name=body.name,
        creator_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return OrgOut(
        id=created.org.id,
        name=created.org.name,
        creator_user_id=created.org.creator_user_id,
        version=created.org.version,
        created_at=created.org.created_at.isoformat(),
        deleted_at=None,
        default_project_id=created.default_project.id,
    )


@router.get("/{org_id}")
async def read_org(
    org_id: uuid.UUID = Path(...),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> OrgOut:
    service = OrgService(db)
    org = await service.get(org_id)
    return OrgOut(
        id=org.id,
        name=org.name,
        creator_user_id=org.creator_user_id,
        version=org.version,
        created_at=org.created_at.isoformat(),
        deleted_at=org.deleted_at.isoformat() if org.deleted_at else None,
    )


@router.patch("/{org_id}")
async def rename_org(
    body: OrgPatchIn,
    org_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_MEMBER_MANAGE, scope_from_path(org_param="org_id"))),
    db: AsyncSession = Depends(db_session),
) -> OrgOut:
    if body.name is None:
        raise HTTPException(status_code=400, detail="nothing to patch")
    try:
        expected = int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=412,
            detail=f"invalid If-Match: {if_match!r}",
        ) from exc
    service = OrgService(db)
    org = await service.rename(
        org_id=org_id,
        new_name=body.name,
        expected_version=expected,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return OrgOut(
        id=org.id,
        name=org.name,
        creator_user_id=org.creator_user_id,
        version=org.version,
        created_at=org.created_at.isoformat(),
        deleted_at=org.deleted_at.isoformat() if org.deleted_at else None,
    )


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_org(
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_DELETE, scope_from_path(org_param="org_id"))),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = OrgService(db)
    await service.soft_delete(
        org_id=org_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/{org_id}/restore", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def restore_org(
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    service = OrgService(db)
    await service.restore(
        org_id=org_id,
        admin_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.get("/{org_id}/members")
async def list_members(
    org_id: uuid.UUID = Path(...),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> list[OrgMemberOut]:
    service = OrgService(db)
    members = await service.list_members(org_id)
    user_ids = [m.user_id for m in members]
    if user_ids:
        from contexts.identity.infrastructure import tables as user_t

        email_rows = (
            await db.execute(
                sa.select(user_t.users.c.id, user_t.users.c.email).where(user_t.users.c.id.in_(user_ids))
            )
        ).all()
        emails: dict[uuid.UUID, str] = {r.id: r.email for r in email_rows}
    else:
        emails = {}
    return [
        OrgMemberOut(
            user_id=m.user_id,
            email=emails.get(m.user_id, ""),
            role=m.role.value,
            is_original_creator=m.is_original_creator,
            joined_at=m.joined_at.isoformat(),
        )
        for m in members
    ]


@router.delete(
    "/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_member(
    org_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_MEMBER_MANAGE, scope_from_path(org_param="org_id"))),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = OrgService(db)
    await service.remove_member(
        org_id=org_id,
        target_user_id=user_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.patch("/{org_id}/members/{user_id}")
async def patch_member(
    body: MemberPatchIn,
    org_id: uuid.UUID = Path(...),
    user_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_OWNER_MANAGE, scope_from_path(org_param="org_id"))),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = OrgService(db)
    await service.change_member_role(
        org_id=org_id,
        target_user_id=user_id,
        new_role=OrgMemberRole(body.role),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"ok": "true"}


@router.post("/{org_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreateIn,
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(Capability.ORG_MEMBER_MANAGE, scope_from_path(org_param="org_id"))),
    db: AsyncSession = Depends(db_session),
) -> InviteOut:
    service = InviteService(db)
    invited = await service.create_org_invite(
        org_id=org_id,
        inviter_user_id=principal.user_id,
        invitee_email=body.email,
        role=OrgMemberRole(body.role),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return InviteOut(
        id=invited.invite.id,
        scope_type=invited.invite.scope_type.value,
        scope_id=invited.invite.scope_id,
        invitee_email=invited.invite.invitee_email,
        role=invited.invite.role,
        state=invited.invite.state.value,
        expires_at=invited.invite.expires_at.isoformat(),
    )


# ---------- OC transfer ---------------------------------------------------


@router.post(
    "/{org_id}/original-creator-transfers",
    status_code=status.HTTP_201_CREATED,
)
async def transfer_initiate(
    body: TransferInitIn,
    org_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> TransferOut:
    service = OCTransferService(db)
    t = await service.initiate(
        org_id=org_id,
        initiator_user_id=principal.user_id,
        target_user_id=body.target_user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _transfer_out(t)


@router.get("/{org_id}/original-creator-transfers")
async def transfer_list(
    org_id: uuid.UUID = Path(...),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> list[TransferOut]:
    service = OCTransferService(db)
    return [_transfer_out(t) for t in await service.list_pending(org_id)]


@router.post(
    "/{org_id}/original-creator-transfers/{transfer_id}/accept",
)
async def transfer_accept(
    org_id: uuid.UUID = Path(...),
    transfer_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> TransferOut:
    service = OCTransferService(db)
    t = await service.accept(
        org_id=org_id,
        transfer_id=transfer_id,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _transfer_out(t)


@router.delete(
    "/{org_id}/original-creator-transfers/{transfer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def transfer_cancel(
    org_id: uuid.UUID = Path(...),
    transfer_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require_membership(org_param="org_id")),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = OCTransferService(db)
    await service.cancel(
        org_id=org_id,
        transfer_id=transfer_id,
        caller_user_id=principal.user_id,
        caller_is_admin=principal.is_admin,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


def _transfer_out(t) -> TransferOut:
    return TransferOut(
        id=t.id,
        org_id=t.org_id,
        initiator_user_id=t.initiator_user_id,
        target_user_id=t.target_user_id,
        state=t.state.value,
        expires_at=t.expires_at.isoformat(),
    )


__all__ = ["router"]
