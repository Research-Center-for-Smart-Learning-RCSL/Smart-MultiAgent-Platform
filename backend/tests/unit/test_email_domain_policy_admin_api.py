"""The Admin email-domain policy surface and its bootstrap (R19a.13).

Covers `docs/tasks/2026-08-30-identity-onboarding-policy-hardening`:

* AC-1  the bootstrap import re-reads under the advisory lock and creates the
        version-1 compatibility row from one legacy snapshot;
* AC-2  a rejected legacy shape leaves no row and is retryable;
* AC-3  GET works in every phase, PUT succeeds only in `active`, and a fenced or
        stale write mutates nothing and emits no audit row;
* AC-13 an activation-era failure leaves the row in compatibility.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import admin_email_domain_policy as policy_mod
from contexts.identity.application.email_domain_bootstrap import (
    BOOTSTRAP_LOCK,
    import_legacy_policy_if_absent,
)
from contexts.identity.application.email_domain_policy_service import EmailDomainPolicyService
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import (
    EmailDomainPolicyRolloutFenced,
    EmailDomainPolicyUnavailable,
    EmailDomainPolicyVersionMismatch,
    InvalidEmailDomain,
    InvalidLegacyEmailDomainPolicy,
)
from contexts.identity.interfaces import error_mapping
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

_ADMIN = uuid.UUID("55555555-5555-5555-5555-555555555555")
_ACTIVE = EmailDomainPolicyRolloutState.ACTIVE
_COMPAT = EmailDomainPolicyRolloutState.COMPATIBILITY
_FROZEN = EmailDomainPolicyRolloutState.ROLLBACK_FROZEN


def _policy(
    *,
    state: EmailDomainPolicyRolloutState = _ACTIVE,
    mode: EmailDomainPolicyMode = EmailDomainPolicyMode.ALLOW,
    allow: frozenset[str] = frozenset({"example.edu"}),
    version: int = 4,
) -> EmailDomainPolicy:
    return EmailDomainPolicy(
        mode=mode,
        allow=allow,
        version=version,
        rollout_state=state,
        updated_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def _service(
    *, stored: EmailDomainPolicy | None, updated: EmailDomainPolicy | None = None
) -> tuple[EmailDomainPolicyService, AsyncMock, AsyncMock]:
    repository, mirror = AsyncMock(), AsyncMock()
    repository.get.return_value = stored
    repository.replace_active.return_value = updated
    return (
        EmailDomainPolicyService(AsyncMock(), repository=repository, mirror=mirror),
        repository,
        mirror,
    )


# ---------------------------------------------------------------------------
# AC-3 - the service's guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [_COMPAT, _FROZEN])
async def test_a_write_outside_active_is_fenced_without_touching_anything(
    state: EmailDomainPolicyRolloutState,
) -> None:
    service, repository, _ = _service(stored=_policy(state=state))

    with (
        patch("shared_kernel.audit.emit", new_callable=AsyncMock) as audit,
        pytest.raises(EmailDomainPolicyRolloutFenced) as exc,
    ):
        await service.replace(
            expected_version=4,
            mode=EmailDomainPolicyMode.DENY,
            allow=[],
            deny=["spam.test"],
            actor_user_id=_ADMIN,
            actor_ip=None,
        )

    assert exc.value.rollout_state == state.value
    repository.replace_active.assert_not_awaited()
    audit.assert_not_awaited()


async def test_a_stale_version_is_reported_as_a_conflict_not_an_overwrite() -> None:
    service, _repository, _ = _service(stored=_policy(version=9), updated=None)

    with pytest.raises(EmailDomainPolicyVersionMismatch, match="version 9"):
        await service.replace(
            expected_version=4,
            mode=EmailDomainPolicyMode.OFF,
            allow=[],
            deny=[],
            actor_user_id=_ADMIN,
            actor_ip=None,
        )


async def test_a_freeze_landing_mid_write_is_reported_as_fenced_not_as_stale() -> None:
    """The guard covers version *and* phase, so a `None` result has to be
    diagnosed rather than assumed to be a version conflict: telling the operator
    to reload and retry would send them into a fence forever."""
    service, repository, _ = _service(stored=_policy(state=_ACTIVE), updated=None)
    repository.get.side_effect = [_policy(state=_ACTIVE), _policy(state=_FROZEN)]

    with pytest.raises(EmailDomainPolicyRolloutFenced):
        await service.replace(
            expected_version=4,
            mode=EmailDomainPolicyMode.OFF,
            allow=[],
            deny=[],
            actor_user_id=_ADMIN,
            actor_ip=None,
        )


async def test_an_invalid_domain_is_rejected_before_the_update() -> None:
    service, repository, _ = _service(stored=_policy())

    with pytest.raises(InvalidEmailDomain):
        await service.replace(
            expected_version=4,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["https://example.edu"],
            deny=[],
            actor_user_id=_ADMIN,
            actor_ip=None,
        )
    repository.replace_active.assert_not_awaited()


async def test_a_committed_write_audits_counts_and_versions_but_never_domains() -> None:
    """A list of institutional domains identifies the institutions a deployment
    serves, and the audit log has a wider readership than the Admin surface."""
    service, _, _ = _service(
        stored=_policy(version=4),
        updated=_policy(version=5, allow=frozenset({"a.edu", "b.edu"})),
    )

    with patch("shared_kernel.audit.emit", new_callable=AsyncMock) as audit:
        await service.replace(
            expected_version=4,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["a.edu", "b.edu"],
            deny=[],
            actor_user_id=_ADMIN,
            actor_ip="9.9.9.9",
        )

    event = audit.await_args.args[1]
    assert event.action == "admin.email_domain_policy_updated"
    assert event.actor_user_id == _ADMIN
    assert event.metadata == {
        "rollout_state": "active",
        "mode": "allow",
        "old_version": 4,
        "new_version": 5,
        "allow_count": 2,
        "deny_count": 0,
    }
    assert "a.edu" not in repr(event.metadata)


async def test_publishing_a_committed_policy_never_fails_the_request() -> None:
    """AC-4: the write is committed by the time `publish` runs, so a mirror
    failure must not tell the Admin their change was rolled back."""
    service, _, mirror = _service(stored=_policy())
    mirror.write.side_effect = ConnectionError("redis is down")

    await service.publish(_policy(version=5))


async def test_a_missing_row_reads_as_unavailable_rather_than_a_permissive_default() -> None:
    service, _, _ = _service(stored=None)

    with pytest.raises(EmailDomainPolicyUnavailable):
        await service.get()


# ---------------------------------------------------------------------------
# AC-3 - the route
# ---------------------------------------------------------------------------


def _app(*, is_admin: bool = True) -> FastAPI:
    app = FastAPI()
    error_mapping.register(app)
    app.include_router(policy_mod.router, prefix="/api/admin")
    principal = Principal(user_id=_ADMIN, is_admin=is_admin, email_verified=True)
    app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
    app.dependency_overrides[db_session] = _fake_session
    return app


def _fake_session() -> AsyncMock:
    """Zero-parameter on purpose: FastAPI reads an override's signature, and
    `AsyncMock` itself turns its constructor keywords into query parameters."""
    return AsyncMock()


def _facade(**attrs: object) -> AsyncMock:
    facade = AsyncMock()
    for name, value in attrs.items():
        getattr(facade, name).return_value = value
    return facade


@pytest.mark.parametrize("state", [_ACTIVE, _COMPAT, _FROZEN])
def test_get_serves_the_policy_and_its_phase_in_every_state(
    state: EmailDomainPolicyRolloutState,
) -> None:
    facade = _facade(get_email_domain_policy=_policy(state=state))
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app()).get("/api/admin/email-domain-policy")

    assert response.status_code == 200
    body = response.json()
    assert body["rollout_state"] == state.value
    assert body["allow"] == ["example.edu"]
    # The UI must be able to render read-only without attempting a write first.
    assert body["editable"] is (state is _ACTIVE)


def test_get_is_refused_for_a_non_admin() -> None:
    facade = _facade(get_email_domain_policy=_policy())
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app(is_admin=False)).get("/api/admin/email-domain-policy")

    assert response.status_code == 403
    facade.get_email_domain_policy.assert_not_awaited()


def test_put_is_refused_for_a_non_admin() -> None:
    facade = _facade(update_email_domain_policy=_policy())
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app(is_admin=False)).put(
            "/api/admin/email-domain-policy",
            headers={"If-Match": "4"},
            json={"mode": "off", "allow": [], "deny": []},
        )

    assert response.status_code == 403
    facade.update_email_domain_policy.assert_not_awaited()


def test_put_replaces_the_policy_and_refreshes_the_mirror_after_committing() -> None:
    facade = _facade(update_email_domain_policy=_policy(version=5))
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app()).put(
            "/api/admin/email-domain-policy",
            headers={"If-Match": "4"},
            json={"mode": "allow", "allow": ["example.edu"], "deny": []},
        )

    assert response.status_code == 200
    assert response.json()["version"] == 5
    assert facade.update_email_domain_policy.await_args.kwargs["expected_version"] == 4
    facade.publish_email_domain_policy.assert_awaited_once()


@pytest.mark.parametrize("header", [None, "not-a-version", ""])
def test_a_missing_or_unparseable_if_match_is_a_conflict_not_a_free_overwrite(
    header: str | None,
) -> None:
    """Treating an unreadable precondition as "no precondition" would defeat the
    point of having one."""
    facade = _facade(update_email_domain_policy=_policy())
    headers = {} if header is None else {"If-Match": header}
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app()).put(
            "/api/admin/email-domain-policy",
            headers=headers,
            json={"mode": "off", "allow": [], "deny": []},
        )

    assert response.status_code == 409
    facade.update_email_domain_policy.assert_not_awaited()


def test_the_fence_and_the_stale_version_are_distinct_409_types() -> None:
    """Different problems with different recoveries: one is fixed by reloading,
    the other only by an operator transition."""
    cases = [
        (EmailDomainPolicyVersionMismatch("stale"), "admin/email-domain-policy-stale"),
        (EmailDomainPolicyRolloutFenced("compatibility"), "admin/email-domain-policy-fenced"),
    ]
    for error, slug in cases:
        facade = AsyncMock()
        facade.update_email_domain_policy.side_effect = error
        with patch.object(policy_mod, "IdentityFacade", return_value=facade):
            response = TestClient(_app()).put(
                "/api/admin/email-domain-policy",
                headers={"If-Match": "4"},
                json={"mode": "off", "allow": [], "deny": []},
            )
        assert response.status_code == 409
        assert response.json()["type"].endswith(slug)


def test_the_fence_names_the_phase_so_the_ui_can_explain_it() -> None:
    facade = AsyncMock()
    facade.update_email_domain_policy.side_effect = EmailDomainPolicyRolloutFenced("rollback_frozen")
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app()).put(
            "/api/admin/email-domain-policy",
            headers={"If-Match": "4"},
            json={"mode": "off", "allow": [], "deny": []},
        )

    assert response.json()["rollout_state"] == "rollback_frozen"


def test_an_invalid_domain_is_a_422_and_an_unavailable_authority_a_503() -> None:
    for error, expected in (
        (InvalidEmailDomain("bad"), 422),
        (EmailDomainPolicyUnavailable("gone"), 503),
    ):
        facade = AsyncMock()
        facade.update_email_domain_policy.side_effect = error
        with patch.object(policy_mod, "IdentityFacade", return_value=facade):
            response = TestClient(_app()).put(
                "/api/admin/email-domain-policy",
                headers={"If-Match": "4"},
                json={"mode": "off", "allow": [], "deny": []},
            )
        assert response.status_code == expected


def test_an_oversized_list_is_rejected_at_the_boundary() -> None:
    facade = _facade(update_email_domain_policy=_policy())
    with patch.object(policy_mod, "IdentityFacade", return_value=facade):
        response = TestClient(_app()).put(
            "/api/admin/email-domain-policy",
            headers={"If-Match": "4"},
            json={"mode": "allow", "allow": ["a.edu"] * 1001, "deny": []},
        )

    assert response.status_code == 422
    facade.update_email_domain_policy.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC-1 / AC-2 / AC-13 - the bootstrap import
# ---------------------------------------------------------------------------


async def test_the_import_takes_the_lock_and_re_reads_under_it() -> None:
    """An unlocked pre-check would be a TOCTOU on the one decision this makes."""
    db, repository, legacy = AsyncMock(), AsyncMock(), AsyncMock()
    repository.get.return_value = None
    legacy.read_snapshot.return_value = _policy(state=_COMPAT, version=0)

    with patch(
        "contexts.identity.application.email_domain_bootstrap.advisory_xact_lock",
        new_callable=AsyncMock,
    ) as lock:
        await import_legacy_policy_if_absent(db, repository=repository, legacy=legacy)

    lock.assert_awaited_once_with(db, BOOTSTRAP_LOCK)
    assert lock.await_args.args[0] is db
    repository.get.assert_awaited_once()
    repository.create_from_legacy.assert_awaited_once()


async def test_the_import_is_skipped_when_a_row_already_exists() -> None:
    """The loser of a concurrent first start observes the winner's row and does
    not try to insert over it."""
    db, repository, legacy = AsyncMock(), AsyncMock(), AsyncMock()
    repository.get.return_value = _policy(state=_COMPAT, version=1)

    with patch(
        "contexts.identity.application.email_domain_bootstrap.advisory_xact_lock",
        new_callable=AsyncMock,
    ):
        await import_legacy_policy_if_absent(db, repository=repository, legacy=legacy)

    legacy.read_snapshot.assert_not_awaited()
    repository.create_from_legacy.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        InvalidLegacyEmailDomainPolicy("config:email_domain:mode holds 'nonsense'"),
        EmailDomainPolicyUnavailable("the legacy email-domain keys are unreadable"),
    ],
)
async def test_a_rejected_or_unreadable_legacy_policy_writes_no_row(failure: Exception) -> None:
    """It propagates, which fails the boot: the caller's transaction rolls back,
    so no row is written and the next start retries cleanly. Continuing would
    come up with no policy authority at all."""
    db, repository, legacy = AsyncMock(), AsyncMock(), AsyncMock()
    repository.get.return_value = None
    legacy.read_snapshot.side_effect = failure

    with (
        patch(
            "contexts.identity.application.email_domain_bootstrap.advisory_xact_lock",
            new_callable=AsyncMock,
        ),
        pytest.raises(type(failure)),
    ):
        await import_legacy_policy_if_absent(db, repository=repository, legacy=legacy)

    repository.create_from_legacy.assert_not_awaited()
