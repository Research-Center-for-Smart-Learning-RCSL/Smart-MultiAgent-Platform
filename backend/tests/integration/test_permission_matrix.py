"""144-case permission matrix — every (Capability × Role) pair.

Covers C.6 / R5.01–R5.05. Exercises the pure `decide()` function with a
fake `RoleResolver`, so the test does not depend on DB/Redis. Each case
pins the expected Outcome shape; specific ∘ (own-only) and OC-only rows
are additionally asserted against concrete scope inputs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from shared_kernel.auth.permissions import (
    Capability,
    Decision,
    Principal,
    Role,
    Scope,
    decide,
)


@dataclass
class FakeResolver:
    roles: frozenset[Role]
    oc: bool = False
    room_participant: bool = False

    async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]:
        return self.roles

    async def is_original_creator(self, *, user_id: uuid.UUID, org_id: uuid.UUID) -> bool:
        return self.oc

    async def is_chatroom_participant(self, *, user_id: uuid.UUID, chatroom_id: uuid.UUID) -> bool:
        return self.room_participant


# Static-assert the matrix has exactly 24 capabilities and 6 roles.
def test_matrix_shape_is_24x6() -> None:
    assert len(list(Capability)) == 24
    assert len(list(Role)) == 6


# Admin is universal ALLOW except for KEY_VIEW_PLAINTEXT (row 1).
@pytest.mark.parametrize("cap", list(Capability))
@pytest.mark.asyncio()
async def test_admin_universal_allow_except_plaintext(cap: Capability) -> None:
    principal = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True)
    resolver = FakeResolver(roles=frozenset())
    scope = Scope(org_id=uuid.uuid4(), resource_owner_user_id=principal.user_id)
    d: Decision = await decide(principal, cap, scope, resolver)
    if cap is Capability.KEY_VIEW_PLAINTEXT:
        assert not d.allowed, "R7.15: plaintext-key-view is universally denied"
    else:
        assert d.allowed, f"Admin must be allowed {cap.value}; reason={d.reason}"


# Full 24×6 — non-admin sweep. Expected allow-set derived from §5.2.
_EXPECTED_ALLOW: dict[Capability, set[Role]] = {
    # (non-admin roles that should get ALLOW or conditional-true with a
    # matching scope; KEY_VIEW_PLAINTEXT has no allowed role at all)
    Capability.KEY_VIEW_PLAINTEXT: set(),
    Capability.KEY_UPLOAD: {Role.ORG_OWNER, Role.ORG_MEMBER, Role.PROJECT_OWNER, Role.PROJECT_MEMBER},
    Capability.KEY_DELETE_OWN: {Role.ORG_OWNER, Role.ORG_MEMBER, Role.PROJECT_OWNER, Role.PROJECT_MEMBER},
    Capability.KEY_DELETE_OTHER_IN_PROJECT: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.KEY_VIEW_USAGE_PROJECT: {
        Role.ORG_OWNER,
        Role.ORG_MEMBER,
        Role.PROJECT_OWNER,
        Role.PROJECT_MEMBER,
    },
    Capability.KEY_CONFIGURE: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.ORG_CREATE: {Role.ORG_OWNER, Role.ORG_MEMBER, Role.PROJECT_OWNER, Role.PROJECT_MEMBER},
    Capability.ORG_DELETE: {Role.ORG_OWNER},  # only if OC
    Capability.ORG_OWNER_MANAGE: {Role.ORG_OWNER},
    Capability.ORG_MEMBER_MANAGE: {Role.ORG_OWNER},
    Capability.PROJECT_CREATE_UNDER_ORG: {Role.ORG_OWNER, Role.ORG_MEMBER},
    Capability.PROJECT_CREATE_UNDER_USER: {
        Role.ORG_OWNER,
        Role.ORG_MEMBER,
        Role.PROJECT_OWNER,
        Role.PROJECT_MEMBER,
    },
    Capability.PROJECT_DELETE: {Role.ORG_OWNER, Role.ORG_MEMBER, Role.PROJECT_OWNER},
    Capability.PROJECT_MEMBER_MANAGE: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.RESOURCE_CREATE_EDIT: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.CHAT_CREATE: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.CHAT_SEND: {
        Role.ORG_OWNER,
        Role.ORG_MEMBER,
        Role.PROJECT_OWNER,
        Role.PROJECT_MEMBER,
        Role.GUEST,
    },
    Capability.GUEST_LINK_MANAGE: {Role.ORG_OWNER, Role.PROJECT_OWNER},
    Capability.CHAT_EXPORT: {
        Role.ORG_OWNER,
        Role.ORG_MEMBER,
        Role.PROJECT_OWNER,
        Role.PROJECT_MEMBER,
        Role.GUEST,
    },
    Capability.MESSAGE_DELETE: {
        Role.ORG_OWNER,
        Role.ORG_MEMBER,
        Role.PROJECT_OWNER,
        Role.PROJECT_MEMBER,
        Role.GUEST,
    },
    Capability.AUDIT_VIEW: set(),
    Capability.USER_BAN: set(),
    Capability.USER_DELETE_ANY: set(),
    Capability.USER_READ_ANY: set(),
}


@pytest.mark.parametrize("cap", list(Capability))
@pytest.mark.parametrize("role", list(Role))
@pytest.mark.asyncio()
async def test_matrix_full_sweep(cap: Capability, role: Role) -> None:
    """Every (capability, role) ⇒ allow iff §5.2 says so.

    For rows that carry ∘ / OC / ROOM_ACL, we pass a scope that *satisfies*
    the side-condition (resource_owner_user_id = caller, oc=True,
    room_participant=True). The broad sweep then collapses to the allow-set
    pinned in `_EXPECTED_ALLOW`.
    """
    user = uuid.uuid4()
    principal = Principal(user_id=user, is_admin=False, email_verified=True)
    # Resolver that grants exactly this single role, and grants every
    # side-condition the matrix might ask for.
    resolver = FakeResolver(
        roles=frozenset({role}),
        oc=True,
        room_participant=True,
    )
    scope = Scope(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        resource_owner_user_id=user,  # satisfies OWN_ONLY
    )
    d = await decide(principal, cap, scope, resolver)
    expected = role in _EXPECTED_ALLOW[cap]
    assert d.allowed is expected, (
        f"{cap.value} × {role.value}: got allowed={d.allowed} "
        f"reason={d.reason}, expected allowed={expected}"
    )


# Targeted: OWN_ONLY denies when the caller does NOT own the resource.
@pytest.mark.asyncio()
async def test_own_only_denies_when_not_owner() -> None:
    caller = uuid.uuid4()
    other = uuid.uuid4()
    principal = Principal(user_id=caller, is_admin=False, email_verified=True)
    resolver = FakeResolver(roles=frozenset({Role.PROJECT_MEMBER}))
    scope = Scope(project_id=uuid.uuid4(), resource_owner_user_id=other)
    d = await decide(principal, Capability.KEY_DELETE_OWN, scope, resolver)
    assert not d.allowed


# Targeted: ORG_DELETE — OrgOwner without OC is denied.
@pytest.mark.asyncio()
async def test_org_delete_requires_original_creator() -> None:
    user = uuid.uuid4()
    principal = Principal(user_id=user, is_admin=False, email_verified=True)
    resolver = FakeResolver(roles=frozenset({Role.ORG_OWNER}), oc=False)
    scope = Scope(org_id=uuid.uuid4())
    d = await decide(principal, Capability.ORG_DELETE, scope, resolver)
    assert not d.allowed


# R6.02: unverified users cannot create Org / Project.
@pytest.mark.asyncio()
async def test_email_unverified_blocks_create_capabilities() -> None:
    principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=False)
    resolver = FakeResolver(roles=frozenset({Role.ORG_OWNER}))
    for cap in (
        Capability.ORG_CREATE,
        Capability.PROJECT_CREATE_UNDER_ORG,
        Capability.PROJECT_CREATE_UNDER_USER,
    ):
        d = await decide(principal, cap, Scope(), resolver)
        assert not d.allowed, f"{cap.value} must gate on email verification"


# R5.01 precedence: Admin beats email-unverified for non-plaintext rows.
@pytest.mark.asyncio()
async def test_admin_not_gated_by_email_verification() -> None:
    principal = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=False)
    resolver = FakeResolver(roles=frozenset())
    d = await decide(principal, Capability.ORG_CREATE, Scope(), resolver)
    assert d.allowed


# ROOM_ACL — GUEST allowed only with room participation proven.
@pytest.mark.asyncio()
async def test_guest_chat_send_requires_room_participation() -> None:
    principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=False)
    scope = Scope(chatroom_id=uuid.uuid4())

    denied = await decide(
        principal,
        Capability.CHAT_SEND,
        scope,
        FakeResolver(roles=frozenset({Role.GUEST}), room_participant=False),
    )
    assert not denied.allowed

    allowed = await decide(
        principal,
        Capability.CHAT_SEND,
        scope,
        FakeResolver(roles=frozenset({Role.GUEST}), room_participant=True),
    )
    assert allowed.allowed
