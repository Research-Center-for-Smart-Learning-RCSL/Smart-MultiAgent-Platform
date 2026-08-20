"""Onboarding without SMTP — the invite half (R6.09, R6.10).

Covers the acceptance criteria of `docs/tasks/2026-08-20-onboarding-without-smtp`:

* AC-1  both invite-create routes return an `accept_url` carrying the plaintext
        token the redeem endpoint consumes;
* AC-2  that response is byte-identical whether or not the invitee's address
        already has an account (no account-existence oracle, Q-5);
* AC-3  no invite *read* path carries `accept_url`;
* AC-4  the invitable-member pool is the parent Org's members minus project
        members minus live pending invites, empty for a user-owned project, and
        refused without capability #14;
* AC-10 nothing writes an `org_members` / `project_members` row outside
        `InviteService._finalize_acceptance`.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import invites as invites_mod
from app.api.v1 import orgs as orgs_mod
from app.api.v1 import projects as projects_mod
from contexts.tenancy.application.invite_service import InvitableMember, InviteService
from contexts.tenancy.domain.models import Invite, InviteScope, InviteState
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, get_role_resolver
from shared_kernel.auth.permissions import Principal, Role
from shared_kernel.db.session import db_session

_ORIGIN = "https://smap.test"
_ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PROJECT = uuid.UUID("22222222-2222-2222-2222-222222222222")
_INVITE = uuid.UUID("33333333-3333-3333-3333-333333333333")
_INVITER = uuid.UUID("44444444-4444-4444-4444-444444444444")
_TOKEN = "fixed-plaintext-token"
_EXPIRES = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


def _invite(scope: InviteScope, scope_id: uuid.UUID) -> Invite:
    return Invite(
        id=_INVITE,
        scope_type=scope,
        scope_id=scope_id,
        role="member",
        inviter_user_id=_INVITER,
        invitee_email="bob@example.com",
        invitee_user_id=None,
        state=InviteState.PENDING,
        token_hash="hash",
        expires_at=_EXPIRES,
        created_at=_EXPIRES - timedelta(days=7),
        resolved_at=None,
    )


class _Resolver:
    """Grants one fixed role set, so `require(...)` runs for real against the
    §5.2 matrix instead of being stubbed out."""

    def __init__(self, roles: frozenset[Role]) -> None:
        self._roles = roles

    async def roles_for(self, principal: Principal, scope: object) -> frozenset[Role]:
        return self._roles

    async def is_original_creator(self, **_: object) -> bool:
        return False

    async def is_chatroom_participant(self, **_: object) -> bool:
        return False


def _app(*, roles: frozenset[Role], db: object) -> FastAPI:
    app = FastAPI()
    app.include_router(orgs_mod.router)
    app.include_router(projects_mod.router)
    principal = Principal(user_id=_INVITER, is_admin=False, email_verified=True)
    app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
    app.dependency_overrides[get_role_resolver] = lambda: _Resolver(roles)
    app.dependency_overrides[db_session] = lambda: db
    return app


def _invite_db(*, invitee_has_account: bool) -> AsyncMock:
    """A session whose two reads inside invite creation are pinned in order.

    `InviteRepository.create` and `audit.emit` are patched out by the caller, so
    the only `execute` calls left are `_notify_invitee`'s user lookup and
    `_scope_name`'s name lookup — in that order.
    """
    user_row = MagicMock(id=uuid.uuid4()) if invitee_has_account else None
    name_row = MagicMock()
    name_row.name = "Acme"
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(first=MagicMock(return_value=user_row)),
            MagicMock(first=MagicMock(return_value=name_row)),
        ]
    )
    return db


def _post_invite(path: str, *, roles: frozenset[Role], invitee_has_account: bool):
    db = _invite_db(invitee_has_account=invitee_has_account)
    scope = InviteScope.ORG if "/orgs/" in path else InviteScope.PROJECT
    scope_id = _ORG if scope is InviteScope.ORG else _PROJECT
    repo = AsyncMock()
    repo.create.return_value = (_TOKEN, _invite(scope, scope_id))

    def _service(session, **_):
        svc = InviteService(session, email_sender=AsyncMock(), public_origin=_ORIGIN)
        svc._invites = repo
        return svc

    with (
        patch.object(orgs_mod, "InviteService", _service),
        patch.object(projects_mod, "InviteService", _service),
        patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock),
        patch(
            "contexts.tenancy.application.invite_service.NotificationFacade",
            return_value=AsyncMock(),
        ),
        patch(
            "contexts.tenancy.application.invite_service.ratelimit.check_raw",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(allowed=True),
        ),
    ):
        client = TestClient(_app(roles=roles, db=db))
        return client.post(path, json={"email": "bob@example.com", "role": "member"})


# ---------------------------------------------------------------------------
# AC-1 — the accept link is returned and carries the redeemable token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "roles"),
    [
        (f"/api/orgs/{_ORG}/invites", frozenset({Role.ORG_OWNER})),
        (f"/api/projects/{_PROJECT}/invites", frozenset({Role.PROJECT_OWNER})),
    ],
)
def test_invite_create_returns_an_accept_url_carrying_the_plaintext_token(
    path: str, roles: frozenset[Role]
) -> None:
    response = _post_invite(path, roles=roles, invitee_has_account=False)

    assert response.status_code == 201
    accept_url = response.json()["accept_url"]
    assert accept_url == f"{_ORIGIN}/?invite=1#token={_TOKEN}"
    # The redeem endpoint reads the token out of the URL *fragment* (SEC-8), so
    # what the SPA will POST to /api/invites/accept-by-token is exactly the
    # plaintext token the service issued.
    assert urlsplit(accept_url).fragment == f"token={_TOKEN}"


# ---------------------------------------------------------------------------
# AC-2 — no account-existence oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "roles"),
    [
        (f"/api/orgs/{_ORG}/invites", frozenset({Role.ORG_OWNER})),
        (f"/api/projects/{_PROJECT}/invites", frozenset({Role.PROJECT_OWNER})),
    ],
)
def test_invite_response_is_identical_whether_or_not_the_address_has_an_account(
    path: str, roles: frozenset[Role]
) -> None:
    with_account = _post_invite(path, roles=roles, invitee_has_account=True)
    without_account = _post_invite(path, roles=roles, invitee_has_account=False)

    assert with_account.status_code == without_account.status_code
    assert with_account.content == without_account.content
    # `date` is a per-response clock reading, not a property of the branch.
    volatile = {"date"}
    assert {k.lower(): v for k, v in with_account.headers.items() if k.lower() not in volatile} == {
        k.lower(): v for k, v in without_account.headers.items() if k.lower() not in volatile
    }


async def test_only_the_in_app_notification_differs_between_the_two_branches() -> None:
    """The service-level counterpart of AC-2: the *sole* observable difference is
    the notification written to an existing invitee, which never reaches the
    inviter's response."""
    results = []
    notified = []
    for has_account in (True, False):
        repo = AsyncMock()
        repo.create.return_value = (_TOKEN, _invite(InviteScope.ORG, _ORG))
        notifier = AsyncMock()
        with (
            patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock),
            patch(
                "contexts.tenancy.application.invite_service.NotificationFacade",
                return_value=notifier,
            ),
            patch(
                "contexts.tenancy.application.invite_service.ratelimit.check_raw",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(allowed=True),
            ),
        ):
            svc = InviteService(
                _invite_db(invitee_has_account=has_account),
                email_sender=AsyncMock(),
                public_origin=_ORIGIN,
            )
            svc._invites = repo
            results.append(
                await svc.create_org_invite(
                    org_id=_ORG,
                    inviter_user_id=_INVITER,
                    invitee_email="bob@example.com",
                    actor_ip=None,
                )
            )
        notified.append(notifier.send.await_count)

    assert results[0] == results[1]
    assert notified == [1, 0]


# ---------------------------------------------------------------------------
# AC-3 — no read path carries the token
# ---------------------------------------------------------------------------


def test_no_invite_read_model_exposes_an_accept_url() -> None:
    """`/api/invites` and the accept/reject responses use their own `InviteOut`,
    which has no `accept_url` field at all — the token cannot be re-read after
    creation, by construction rather than by a route remembering to omit it."""
    assert "accept_url" not in invites_mod.InviteOut.model_fields
    rendered = invites_mod._to_out(_invite(InviteScope.ORG, _ORG), "Acme")
    assert "accept_url" not in rendered.model_dump()


def test_accept_url_is_optional_and_defaults_to_absent_on_the_create_models() -> None:
    """Nothing but the create route may populate it, so the default is None on
    both create models rather than a required field someone must remember."""
    for model in (orgs_mod.InviteOut, projects_mod.ProjectInviteOut):
        field = model.model_fields["accept_url"]
        assert field.default is None
        assert not field.is_required()


# ---------------------------------------------------------------------------
# AC-4 — the invitable-member pool
# ---------------------------------------------------------------------------


def test_invitable_members_returns_the_pool_to_a_project_owner() -> None:
    pool = [
        InvitableMember(user_id=uuid.uuid4(), email="a@example.com"),
        InvitableMember(user_id=uuid.uuid4(), email="b@example.com"),
    ]
    facade = AsyncMock()
    facade.invitable_project_members = AsyncMock(return_value=pool)
    with patch.object(projects_mod, "TenancyFacade", return_value=facade):
        client = TestClient(_app(roles=frozenset({Role.PROJECT_OWNER}), db=AsyncMock()))
        response = client.get(f"/api/projects/{_PROJECT}/invitable-members")

    assert response.status_code == 200
    assert [row["email"] for row in response.json()] == ["a@example.com", "b@example.com"]
    facade.invitable_project_members.assert_awaited_once_with(_PROJECT)


def test_invitable_members_is_200_and_empty_for_a_project_with_no_parent_org() -> None:
    """An absent pool is a state, not a missing resource — never a 404."""
    facade = AsyncMock()
    facade.invitable_project_members = AsyncMock(return_value=[])
    with patch.object(projects_mod, "TenancyFacade", return_value=facade):
        client = TestClient(_app(roles=frozenset({Role.PROJECT_OWNER}), db=AsyncMock()))
        response = client.get(f"/api/projects/{_PROJECT}/invitable-members")

    assert response.status_code == 200
    assert response.json() == []


def test_invitable_members_refuses_a_caller_without_capability_14() -> None:
    facade = AsyncMock()
    facade.invitable_project_members = AsyncMock(return_value=[])
    with patch.object(projects_mod, "TenancyFacade", return_value=facade):
        client = TestClient(_app(roles=frozenset({Role.PROJECT_MEMBER}), db=AsyncMock()))
        response = client.get(f"/api/projects/{_PROJECT}/invitable-members")

    assert response.status_code == 403
    facade.invitable_project_members.assert_not_awaited()


async def test_invitable_pool_query_excludes_members_and_live_pending_invites() -> None:
    """The pool's two anti-joins and its parent-Org resolution are in the SQL, so
    assert on the compiled statement: a project id from the path, never an org id
    from the caller."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    svc = InviteService(db, email_sender=AsyncMock(), public_origin=_ORIGIN)

    assert await svc.invitable_org_members(_PROJECT) == []

    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "org_members" in sql
    assert "projects.owner_org_id = org_members.org_id" in sql
    assert sql.count("NOT (EXISTS") == 2
    assert "project_members.user_id = org_members.user_id" in sql
    assert "invites.scope_type = 'project'" in sql
    assert "invites.state = 'pending'" in sql
    assert "lower(invites.invitee_email) = lower(users.email)" in sql
    # `literal_binds` renders a UUID without its dashes.
    assert f"projects.id = '{_PROJECT.hex}'" in sql


async def test_invitable_pool_maps_rows_to_user_id_and_email() -> None:
    uid = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[SimpleNamespace(user_id=uid, email="a@example.com")])
        )
    )
    svc = InviteService(db, email_sender=AsyncMock(), public_origin=_ORIGIN)

    assert await svc.invitable_org_members(_PROJECT) == [InvitableMember(user_id=uid, email="a@example.com")]


# ---------------------------------------------------------------------------
# AC-10 — consent invariant
# ---------------------------------------------------------------------------

_MEMBERSHIP_REPOSITORIES = {"OrgMemberRepository", "ProjectMemberRepository"}

_MEMBERSHIP_ADD_CALLERS = {
    # The one sanctioned writer on the request path. Every membership row a user
    # did not create for themselves is written here, after they accepted an
    # invite (Q-1).
    ("invite_service.py", "_finalize_acceptance"),
    # Org/project creation seeds the *creator's own* membership — self-consent by
    # construction, not somebody else placing a user.
    ("org_service.py", "create"),
    ("project_service.py", "create"),
    # Dev/E2E fixture seeding, never reachable from an HTTP request.
    ("seed.py", "_seed_fixtures"),
}


def _membership_add_call_sites() -> set[tuple[str, str]]:
    """Every enclosing function that calls `.add(...)` on an org/project member
    repository, however the repository was bound (attribute, local, or inline)."""
    root = pathlib.Path(__file__).resolve().parents[2]
    found: set[tuple[str, str]] = set()
    for tree_root in ("contexts", "app", "smap"):
        for path in (root / tree_root).rglob("*.py"):
            module = ast.parse(path.read_text(encoding="utf-8"))
            bound = _names_bound_to_membership_repos(module)
            for node in ast.walk(module):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for inner in ast.walk(node):
                    if _is_membership_add(inner, bound):
                        found.add((path.name, node.name))
    return found


def _names_bound_to_membership_repos(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            continue
        if value.func.id not in _MEMBERSHIP_REPOSITORIES:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _is_membership_add(node: ast.AST, bound: set[str]) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr != "add":
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in bound
    if isinstance(receiver, ast.Name):
        return receiver.id in bound
    # `await OrgMemberRepository(db).add(...)` — no intermediate binding.
    return (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id in _MEMBERSHIP_REPOSITORIES
    )


def test_membership_rows_are_written_only_where_consent_was_given() -> None:
    """AC-10. A future shortcut that places a user in an Org or Project without
    their acceptance has to break this test deliberately — which is the point:
    an Org Owner is a Project Owner on every project of the Org (R8.08), so a
    unilateral add hands somebody read access to the added user's work."""
    assert _membership_add_call_sites() == _MEMBERSHIP_ADD_CALLERS
