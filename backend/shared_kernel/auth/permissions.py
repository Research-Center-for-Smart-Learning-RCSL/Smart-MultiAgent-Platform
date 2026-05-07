"""Single-source permission matrix (§5.2, R5.01–R5.05).

Every `require(...)` call funnels through this module — no context duplicates
the check. Resolution runs in three steps:

1. Identify the *authoritative* role the caller holds for the target resource's
   scope (Org → Project → Chat Room). This is done by a `RoleResolver`
   protocol implemented by the tenancy context. The shared_kernel only sees
   the protocol so SoC is preserved.

2. Consult the 24×6 capability matrix. `∘` entries become a callable that the
   caller must evaluate against the concrete resource (e.g. "only if owner").

3. Admin wins over everything (R5.01). Original-Creator immovability is
   enforced by the Org facade, not here — this module's only OC-specific
   check is the *demote OC* path in cap #9 (`org.owner.manage`).

SoC boundary: this module imports **nothing** from `contexts.*`. It exposes
`RoleResolver`, `Principal`, `Capability`, `Role`, `Scope`, and two decisions:

- `is_allowed(principal, capability, scope, resolver) -> Decision`
- `require(capability, scope)` — FastAPI `Depends` factory (lives in
  `dependencies.py` to keep this module framework-free).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Protocol


class Role(str, enum.Enum):
    ADMIN = "admin"
    ORG_OWNER = "org_owner"
    ORG_MEMBER = "org_member"
    PROJECT_OWNER = "project_owner"
    PROJECT_MEMBER = "project_member"
    GUEST = "guest"


class Capability(str, enum.Enum):
    # Keys
    KEY_VIEW_PLAINTEXT = "key.view_plaintext"  # 1  (universal deny)
    KEY_UPLOAD = "key.upload"  # 2
    KEY_DELETE_OWN = "key.delete_own"  # 3
    KEY_DELETE_OTHER_IN_PROJECT = "key.delete_other"  # 4
    KEY_VIEW_USAGE_PROJECT = "key.view_usage"  # 5
    KEY_CONFIGURE = "key.configure"  # 6
    # Orgs
    ORG_CREATE = "org.create"  # 7
    ORG_DELETE = "org.delete"  # 8  (OC or Admin)
    ORG_OWNER_MANAGE = "org.owner.manage"  # 9
    ORG_MEMBER_MANAGE = "org.member.manage"  # 10
    # Projects
    PROJECT_CREATE_UNDER_ORG = "project.create_under_org"  # 11
    PROJECT_CREATE_UNDER_USER = "project.create_under_user"  # 12
    PROJECT_DELETE = "project.delete"  # 13
    PROJECT_MEMBER_MANAGE = "project.member.manage"  # 14
    # Resources
    RESOURCE_CREATE_EDIT = "resource.create_edit"  # 15  (Agent/KG/RAG)
    CHAT_CREATE = "chat.create"  # 16  (WS/Chatroom/Workflow)
    CHAT_SEND = "chat.send"  # 17  (room ACL)
    GUEST_LINK_MANAGE = "guest_link.manage"  # 18
    CHAT_EXPORT = "chat.export"  # 19
    MESSAGE_DELETE = "message.delete"  # 20
    # Admin-only
    AUDIT_VIEW = "audit.view"  # 21
    USER_BAN = "user.ban"  # 22
    USER_DELETE_ANY = "user.delete_any"  # 23
    USER_READ_ANY = "user.read_any"  # 24


# ---------------------------------------------------------------------------
# Scope — the smallest resource "shape" that fully constrains a check.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    """Target resource location for a permission check.

    At most one of the ID fields is populated for a leaf resource; any parents
    are resolved by the tenancy facade at check time (Project → Org).

    `owner_user_id`, `created_by_user_id`, `project_owner_id` carry ∘-entry
    context so the "own resource only" rows in §5.2 can be evaluated without
    a second round-trip.
    """

    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    chatroom_id: uuid.UUID | None = None
    resource_owner_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    is_admin: bool
    email_verified: bool


# ---------------------------------------------------------------------------
# Role resolver — implemented in contexts.tenancy.interfaces.
# ---------------------------------------------------------------------------


class RoleResolver(Protocol):
    """Return the roles the principal has on the given scope.

    Returns a *set* because a single user can simultaneously be, e.g., an Org
    Owner AND a Project Member on the same scope (the inheritance is computed
    here, not stored — R5.03).
    """

    async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]: ...

    async def is_original_creator(self, *, user_id: uuid.UUID, org_id: uuid.UUID) -> bool: ...

    async def is_chatroom_participant(self, *, user_id: uuid.UUID, chatroom_id: uuid.UUID) -> bool: ...


# ---------------------------------------------------------------------------
# Matrix — (capability, role) -> Decision.
# ---------------------------------------------------------------------------


class Outcome(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    OWN_ONLY = "own_only"  # ∘ — caller must own the resource
    ROOM_ACL = "room_acl"  # chat send/export permitted per room ACL
    ORIGINAL_CREATOR_ONLY = "oc"  # #8 — OC (or Admin) only
    NOT_ORIGINAL_CREATOR = "not_oc"  # #9 — target cannot be OC
    ADMIN_ONLY_IF_NOT_OWN = "admin_not_own"  # unused today; future-proof


# Rows are Capability; columns are Role; missing entries default to DENY.
# Admin is universal ALLOW (handled outside the table) unless row #1 which is
# "no one, ever".
_MATRIX: dict[Capability, dict[Role, Outcome]] = {
    Capability.KEY_VIEW_PLAINTEXT: dict.fromkeys(Role, Outcome.DENY),
    Capability.KEY_UPLOAD: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.ALLOW,
    },
    Capability.KEY_DELETE_OWN: {
        Role.ORG_OWNER: Outcome.OWN_ONLY,
        Role.ORG_MEMBER: Outcome.OWN_ONLY,
        Role.PROJECT_OWNER: Outcome.OWN_ONLY,
        Role.PROJECT_MEMBER: Outcome.OWN_ONLY,
    },
    Capability.KEY_DELETE_OTHER_IN_PROJECT: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.KEY_VIEW_USAGE_PROJECT: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.ALLOW,
    },
    Capability.KEY_CONFIGURE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    # Row 7 — *only* Admin may create Orgs *from nothing*. Non-admins create
    # their own Org using `POST /api/orgs` which is governed by email-verify
    # gate rather than by role. Per §5.2 the non-admin allowance flows through
    # "any verified Individual" (R8.01). We encode that as `EMAIL_VERIFIED`:
    Capability.ORG_CREATE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.ALLOW,
    },
    Capability.ORG_DELETE: {
        Role.ORG_OWNER: Outcome.ORIGINAL_CREATOR_ONLY,
    },
    Capability.ORG_OWNER_MANAGE: {
        Role.ORG_OWNER: Outcome.NOT_ORIGINAL_CREATOR,
    },
    Capability.ORG_MEMBER_MANAGE: {
        Role.ORG_OWNER: Outcome.ALLOW,
    },
    Capability.PROJECT_CREATE_UNDER_ORG: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.ALLOW,
    },
    Capability.PROJECT_CREATE_UNDER_USER: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.ALLOW,
    },
    Capability.PROJECT_DELETE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.OWN_ONLY,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.PROJECT_MEMBER_MANAGE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.RESOURCE_CREATE_EDIT: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.CHAT_CREATE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.CHAT_SEND: {
        Role.ORG_OWNER: Outcome.ROOM_ACL,
        Role.ORG_MEMBER: Outcome.ROOM_ACL,
        Role.PROJECT_OWNER: Outcome.ROOM_ACL,
        Role.PROJECT_MEMBER: Outcome.ROOM_ACL,
        Role.GUEST: Outcome.ROOM_ACL,
    },
    Capability.GUEST_LINK_MANAGE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.PROJECT_OWNER: Outcome.ALLOW,
    },
    Capability.CHAT_EXPORT: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.OWN_ONLY,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.OWN_ONLY,
        Role.GUEST: Outcome.OWN_ONLY,
    },
    Capability.MESSAGE_DELETE: {
        Role.ORG_OWNER: Outcome.ALLOW,
        Role.ORG_MEMBER: Outcome.OWN_ONLY,
        Role.PROJECT_OWNER: Outcome.ALLOW,
        Role.PROJECT_MEMBER: Outcome.OWN_ONLY,
        Role.GUEST: Outcome.OWN_ONLY,
    },
    # Rows 21–24 are Admin-only — empty entries ⇒ deny for all non-admin roles.
    Capability.AUDIT_VIEW: {},
    Capability.USER_BAN: {},
    Capability.USER_DELETE_ANY: {},
    Capability.USER_READ_ANY: {},
}


# ---------------------------------------------------------------------------
# Decision object.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    outcome: Outcome | None = None

    @classmethod
    def allow(cls, reason: str, outcome: Outcome = Outcome.ALLOW) -> Decision:
        return cls(True, reason, outcome)

    @classmethod
    def deny(cls, reason: str, outcome: Outcome = Outcome.DENY) -> Decision:
        return cls(False, reason, outcome)


async def decide(
    principal: Principal,
    capability: Capability,
    scope: Scope,
    resolver: RoleResolver,
) -> Decision:
    """Compute a Decision without raising.

    `∘` outcomes require the caller to pass `resource_owner_user_id` in
    `scope`; we can't second-guess that from here without a DB lookup that
    the caller may have already done.
    """
    # Row 1 — never allowed to anyone, not even Admin (R7.15).
    if capability is Capability.KEY_VIEW_PLAINTEXT:
        return Decision.deny("plaintext key display is universally denied")

    if principal.is_admin:
        return Decision.allow("admin bypass", Outcome.ALLOW)

    # R6.02 gate — verified-email prerequisites on Org/Project create + Guest accept.
    if capability in _EMAIL_VERIFICATION_REQUIRED and not principal.email_verified:
        return Decision.deny("email verification required", Outcome.DENY)

    roles = await resolver.roles_for(principal, scope)
    if not roles:
        return Decision.deny("no applicable role in scope")

    row = _MATRIX.get(capability, {})
    for role in roles:
        outcome = row.get(role)
        if outcome is None:
            continue
        if outcome is Outcome.ALLOW:
            return Decision.allow(f"{role.value}→ALLOW", outcome)
        if outcome is Outcome.DENY:
            continue
        if outcome is Outcome.OWN_ONLY:
            if scope.resource_owner_user_id is not None and scope.resource_owner_user_id == principal.user_id:
                return Decision.allow(f"{role.value}→OWN", outcome)
            continue
        if outcome is Outcome.ORIGINAL_CREATOR_ONLY:
            if scope.org_id and await resolver.is_original_creator(
                user_id=principal.user_id, org_id=scope.org_id
            ):
                return Decision.allow("original-creator", outcome)
            continue
        if outcome is Outcome.NOT_ORIGINAL_CREATOR:
            # Gate means: owner may manage *other* owners but not touch the OC;
            # the specific target is checked by the Org facade. Matrix-wise,
            # access is granted; the facade enforces the target-side check.
            return Decision.allow(f"{role.value}→owner-mgmt", outcome)
        if outcome is Outcome.ROOM_ACL:
            if scope.chatroom_id and await resolver.is_chatroom_participant(
                user_id=principal.user_id, chatroom_id=scope.chatroom_id
            ):
                return Decision.allow("room-ACL", outcome)
            continue

    return Decision.deny(f"no matching role in {', '.join(r.value for r in roles)}")


# Capabilities the caller must have email_verified=True to use (R6.02).
_EMAIL_VERIFICATION_REQUIRED: frozenset[Capability] = frozenset(
    {
        Capability.ORG_CREATE,
        Capability.PROJECT_CREATE_UNDER_ORG,
        Capability.PROJECT_CREATE_UNDER_USER,
    }
)


__all__ = [
    "Capability",
    "Decision",
    "Outcome",
    "Principal",
    "Role",
    "RoleResolver",
    "Scope",
    "decide",
]
