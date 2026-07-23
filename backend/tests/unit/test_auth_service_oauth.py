"""Unit tests for the Google OAuth path (R6.14-R6.17).

Two layers, both fully mocked (no network, no DB, no Redis):
  * the OIDC adapter — id_token verification (AC-7), PKCE, single-use state store
  * AuthService orchestration — the resolution table (AC-2..AC-6, AC-9, AC-16)
    and link/unlink (AC-10, AC-11)
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.exc import IntegrityError

from contexts.identity.application.auth_service import AuthService
from contexts.identity.domain.errors import (
    AccountBanned,
    AccountDeleted,
    GoogleEmailUnverified,
    InvalidCredentials,
    LastCredentialError,
    OAuthExchangeFailed,
    OAuthIdentityConflict,
    OAuthUnavailable,
)
from contexts.identity.domain.models import AuthIdentity, User, UserStatus
from contexts.identity.infrastructure.oauth import google as g
from shared_kernel.auth.password import PasswordHasher

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_HASHER = PasswordHasher()
_HASH = _HASHER.hash("Str0ng!Pass#1")
_CLIENT_ID = "client-123.apps.googleusercontent.com"

# One RSA keypair for signing/verifying test id_tokens.
_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIV_PEM = _PRIV.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUB = _PRIV.public_key()


def _make_user(
    *,
    status: UserStatus = UserStatus.ACTIVE,
    email: str = "u@example.com",
    email_verified: bool = True,
    password_hash: str | None = _HASH,
    display_name: str | None = None,
) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash=password_hash,
        email_verified=email_verified,
        status=status,
        banned_reason="nope" if status is UserStatus.BANNED else None,
        banned_at=None,
        deleted_at=None,
        last_login_at=None,
        version=1,
        created_at=_NOW,
        display_name=display_name,
    )


def _identity(user_id: uuid.UUID, sub: str = "sub-1") -> AuthIdentity:
    return AuthIdentity(
        id=uuid.uuid4(),
        user_id=user_id,
        provider="google",
        provider_subject=sub,
        email="u@example.com",
        created_at=_NOW,
    )


def _profile(**over: object) -> g.GoogleProfile:
    base: dict = {"sub": "sub-1", "email": "u@example.com", "email_verified": True, "name": "U"}
    base.update(over)
    return g.GoogleProfile(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Adapter — id_token verification (AC-7)
# ---------------------------------------------------------------------------


def _token(**over: object) -> str:
    ts = int(time.time())
    claims: dict = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "sub": "sub-1",
        "email": "u@example.com",
        "email_verified": True,
        "name": "U",
        "nonce": "nonce-1",
        "iat": ts,
        "exp": ts + 3600,
    }
    claims.update(over)
    return jwt.encode(claims, _PRIV_PEM, algorithm="RS256")


def _client() -> g.GoogleOidcClient:
    return g.GoogleOidcClient(
        client_id=_CLIENT_ID,
        client_secret="secret",
        redirect_uri="https://smap.test/api/auth/google/callback",
        timeout_s=5,
    )


class TestVerifyIdToken:
    def _patch_jwks(self):
        key = MagicMock()
        key.key = _PUB
        jwks = MagicMock()
        jwks.get_signing_key_from_jwt.return_value = key
        return patch.object(g, "_get_jwks_client", return_value=jwks)

    async def test_valid_token_returns_profile(self) -> None:
        with self._patch_jwks():
            prof = await _client().verify_id_token(_token(), nonce="nonce-1")
        assert prof.sub == "sub-1"
        assert prof.email == "u@example.com"
        assert prof.email_verified is True
        assert prof.name == "U"

    async def test_wrong_audience_rejected(self) -> None:
        with self._patch_jwks(), pytest.raises(g.GoogleOAuthError):
            await _client().verify_id_token(_token(aud="someone-else"), nonce="nonce-1")

    async def test_expired_rejected(self) -> None:
        ts = int(time.time())
        with self._patch_jwks(), pytest.raises(g.GoogleOAuthError):
            await _client().verify_id_token(_token(iat=ts - 7200, exp=ts - 3600), nonce="nonce-1")

    async def test_bad_issuer_rejected(self) -> None:
        with self._patch_jwks(), pytest.raises(g.GoogleOAuthError):
            await _client().verify_id_token(_token(iss="https://evil.example"), nonce="nonce-1")

    async def test_nonce_mismatch_rejected(self) -> None:
        with self._patch_jwks(), pytest.raises(g.GoogleOAuthError):
            await _client().verify_id_token(_token(nonce="attacker"), nonce="nonce-1")

    async def test_non_rs256_alg_rejected(self) -> None:
        # An HS256 token must be rejected because verification pins
        # algorithms=["RS256"] — the alg is checked before the key is used, so an
        # attacker cannot downgrade to a symmetric alg (alg-confusion defense).
        forged = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": _CLIENT_ID,
                "sub": "sub-1",
                "email": "u@example.com",
                "email_verified": True,
                "nonce": "nonce-1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            "attacker-symmetric-secret",
            algorithm="HS256",
        )
        with self._patch_jwks(), pytest.raises(g.GoogleOAuthError):
            await _client().verify_id_token(forged, nonce="nonce-1")


# ---------------------------------------------------------------------------
# Adapter — PKCE + single-use state store
# ---------------------------------------------------------------------------


class TestPkceAndState:
    def test_pkce_pair_is_s256(self) -> None:
        import base64
        import hashlib

        verifier, challenge = g.generate_pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected
        assert "=" not in challenge

    async def test_state_store_pop_is_single_use(self) -> None:
        redis = AsyncMock()
        stored = {}

        async def _set(key, val, ex=None):
            stored[key] = val

        async def _getdel(key):
            return stored.pop(key, None)

        redis.set.side_effect = _set
        redis.getdel.side_effect = _getdel
        with patch.object(g, "get_redis", return_value=redis):
            store = g.OAuthStateStore(600)
            await store.put("st", g.OAuthState("verifier", "nonce", "login", None))
            first = await store.pop("st")
            second = await store.pop("st")
        assert first is not None
        assert first.code_verifier == "verifier"
        assert first.mode == "login"
        assert second is None  # single-use


# ---------------------------------------------------------------------------
# Service — resolution table
# ---------------------------------------------------------------------------


class _FakeSavepoint:
    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False  # never swallow — let IntegrityError propagate to the caller


def _make_service(
    *, users: AsyncMock, identities: AsyncMock, sessions: AsyncMock | None = None
) -> AuthService:
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=_FakeSavepoint())
    svc = AuthService(db=db, hasher=_HASHER, email_sender=AsyncMock(), public_origin="https://smap.test")
    svc._users = users
    svc._identities = identities
    svc._sessions = sessions or AsyncMock()
    svc._admins = AsyncMock()
    svc._admins.is_admin.return_value = False
    svc._reset = AsyncMock()
    svc._reset.issue.return_value = ("reset-tok", MagicMock())
    svc._notifier = AsyncMock()
    return svc


@pytest.fixture(autouse=True)
def _patch_session_mint():
    """Patch the _establish_session backends (Vault JWT + Redis session + audit)."""
    with (
        patch("contexts.identity.application.auth_service.jwt.sign_access_token") as mj,
        patch(
            "contexts.identity.application.auth_service.tokens.create_session",
            new_callable=AsyncMock,
        ) as mt,
        patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock),
    ):
        claims = MagicMock()
        claims.jti = uuid.uuid4()
        claims.remaining_ttl.return_value = timedelta(seconds=900)
        mj.return_value = ("access-tok", claims)
        record = MagicMock()
        record.family_id = uuid.uuid4()
        record.expires_at = _NOW + timedelta(days=7)
        mt.return_value = ("refresh-tok", record)
        yield


async def _login(svc: AuthService):
    return await svc.complete_google_login(
        code="code", code_verifier="v", nonce="n", remote_ip="1.2.3.4", user_agent="t"
    )


class TestResolveOauthLogin:
    async def test_known_identity_logs_in(self) -> None:
        # AC-3: existing (provider, sub) -> log that user in, no new user/identity.
        user = _make_user()
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = _identity(user.id)
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        outcome = await _login(svc)

        assert outcome.user.id == user.id
        assert outcome.tokens.access_token == "access-tok"
        users.insert.assert_not_called()
        identities.insert.assert_not_called()
        identities.update_email.assert_awaited_once()

    async def test_new_user_provisioned(self) -> None:
        # AC-2: no identity, no account -> provision passwordless+verified+active.
        new_user = _make_user(status=UserStatus.ACTIVE, email_verified=True, password_hash=None)
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = None
        users = AsyncMock()
        users.get_active_by_email.return_value = None
        users.insert.return_value = new_user
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile(name="New Name"))

        outcome = await _login(svc)

        assert outcome.user.id == new_user.id
        insert_kwargs = users.insert.await_args.kwargs
        assert insert_kwargs["password_hash"] is None
        assert insert_kwargs["email_verified"] is True
        assert insert_kwargs["status"] is UserStatus.ACTIVE
        assert insert_kwargs["display_name"] == "New Name"
        identities.insert.assert_awaited_once()

    async def test_verified_account_auto_links(self) -> None:
        # AC-4: email matches an already-verified account -> auto-link, password intact.
        existing = _make_user(email_verified=True)
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = None
        users = AsyncMock()
        users.get_active_by_email.return_value = existing
        users.get_by_id.return_value = existing
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        outcome = await _login(svc)

        assert outcome.user.id == existing.id
        identities.insert.assert_awaited_once()
        users.mark_verified.assert_not_called()
        users.neutralize_password.assert_not_called()

    async def test_unverified_account_binds_and_neutralizes(self) -> None:
        # AC-5: email matches an UNVERIFIED account -> bind, verify, neutralize the
        # old password, drop sessions, and mail a set-password link.
        existing = _make_user(email_verified=False, status=UserStatus.PENDING)
        verified = _make_user(email_verified=True, status=UserStatus.ACTIVE, password_hash=None)
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = None
        users = AsyncMock()
        users.get_active_by_email.return_value = existing
        users.get_by_id.return_value = verified
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())
        svc._invalidate_user_sessions = AsyncMock()

        outcome = await _login(svc)

        assert outcome.user.id == verified.id
        identities.insert.assert_awaited_once()
        users.mark_verified.assert_awaited_once()
        users.neutralize_password.assert_awaited_once()
        svc._invalidate_user_sessions.assert_awaited_once()
        svc._reset.issue.assert_awaited_once()
        svc._notifier.send_google_linked_password_disabled.assert_awaited_once()

    async def test_google_unverified_email_rejected(self) -> None:
        # AC-6: Google reports email_verified=false -> reject, no writes.
        svc = _make_service(users=AsyncMock(), identities=AsyncMock())
        # Exercise the real _verify_google guard with a fake client.
        client = MagicMock()
        client.exchange_code = AsyncMock(return_value="idtok")
        client.verify_id_token = AsyncMock(return_value=_profile(email_verified=False))
        with (
            patch.object(g, "build_google_client", return_value=client),
            pytest.raises(GoogleEmailUnverified),
        ):
            await svc._verify_google(code="c", code_verifier="v", nonce="n")

    async def test_not_configured_is_unavailable(self) -> None:
        svc = _make_service(users=AsyncMock(), identities=AsyncMock())
        with (
            patch.object(g, "build_google_client", side_effect=g.GoogleOAuthNotConfigured()),
            pytest.raises(OAuthUnavailable),
        ):
            await svc._verify_google(code="c", code_verifier="v", nonce="n")

    async def test_unreachable_is_unavailable(self) -> None:
        svc = _make_service(users=AsyncMock(), identities=AsyncMock())
        client = MagicMock()
        client.exchange_code = AsyncMock(side_effect=g.GoogleOAuthUnreachable())
        with (
            patch.object(g, "build_google_client", return_value=client),
            pytest.raises(OAuthUnavailable),
        ):
            await svc._verify_google(code="c", code_verifier="v", nonce="n")

    async def test_verify_failure_is_exchange_failed(self) -> None:
        svc = _make_service(users=AsyncMock(), identities=AsyncMock())
        client = MagicMock()
        client.exchange_code = AsyncMock(return_value="idtok")
        client.verify_id_token = AsyncMock(side_effect=g.GoogleOAuthError())
        with (
            patch.object(g, "build_google_client", return_value=client),
            pytest.raises(OAuthExchangeFailed),
        ):
            await svc._verify_google(code="c", code_verifier="v", nonce="n")

    @pytest.mark.parametrize("status", [UserStatus.BANNED, UserStatus.DELETED])
    async def test_banned_or_deleted_rejected(self, status: UserStatus) -> None:
        # AC-9: a banned/deleted account cannot obtain a session via Google.
        user = _make_user(status=status)
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = _identity(user.id)
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        expected = AccountBanned if status is UserStatus.BANNED else AccountDeleted
        with pytest.raises(expected):
            await _login(svc)
        svc._sessions.insert.assert_not_called()

    async def test_concurrent_provision_retries_to_winner(self) -> None:
        # AC-16: two concurrent first-time callbacks -> the loser's insert hits the
        # unique constraint, and the retry re-resolves to the winning identity.
        winner = _make_user()
        identities = AsyncMock()
        identities.get_by_provider_subject.side_effect = [None, _identity(winner.id)]
        users = AsyncMock()
        users.get_active_by_email.return_value = None
        users.insert.side_effect = IntegrityError("stmt", {}, Exception("dup"))
        users.get_by_id.return_value = winner
        svc = _make_service(users=users, identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        outcome = await _login(svc)

        assert outcome.user.id == winner.id
        assert identities.get_by_provider_subject.await_count == 2


# ---------------------------------------------------------------------------
# Service — link / unlink (AC-10, AC-11)
# ---------------------------------------------------------------------------


class TestLinkUnlink:
    async def _link(self, svc: AuthService, user_id: uuid.UUID) -> None:
        await svc.complete_google_link(
            user_id=user_id, code="c", code_verifier="v", nonce="n", remote_ip="1.2.3.4"
        )

    async def test_link_inserts_identity(self) -> None:
        uid = uuid.uuid4()
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = None
        svc = _make_service(users=AsyncMock(), identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        await self._link(svc, uid)

        identities.insert.assert_awaited_once()
        assert identities.insert.await_args.kwargs["user_id"] == uid

    async def test_link_conflict_when_bound_elsewhere(self) -> None:
        # AC-10: the Google account is already linked to a different user -> 409.
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = _identity(uuid.uuid4())
        svc = _make_service(users=AsyncMock(), identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        with pytest.raises(OAuthIdentityConflict):
            await self._link(svc, uuid.uuid4())
        identities.insert.assert_not_called()

    async def test_link_idempotent_for_same_user(self) -> None:
        uid = uuid.uuid4()
        identities = AsyncMock()
        identities.get_by_provider_subject.return_value = _identity(uid)
        svc = _make_service(users=AsyncMock(), identities=identities)
        svc._verify_google = AsyncMock(return_value=_profile())

        await self._link(svc, uid)  # no raise
        identities.insert.assert_not_called()

    async def test_unlink_last_credential_refused(self) -> None:
        # AC-11: passwordless account with only Google -> refuse.
        user = _make_user(password_hash=None)
        identities = AsyncMock()
        identities.list_for_user.return_value = [_identity(user.id)]
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(users=users, identities=identities)

        with pytest.raises(LastCredentialError):
            await svc.unlink_google(user_id=user.id, remote_ip="1.2.3.4")
        identities.delete.assert_not_called()

    async def test_unlink_succeeds_with_password(self) -> None:
        # AC-11: has a password -> unlink allowed.
        user = _make_user(password_hash=_HASH)
        identities = AsyncMock()
        identities.list_for_user.return_value = [_identity(user.id)]
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(users=users, identities=identities)

        await svc.unlink_google(user_id=user.id, remote_ip="1.2.3.4")
        identities.delete.assert_awaited_once()

    async def test_unlink_unknown_user_raises(self) -> None:
        users = AsyncMock()
        users.get_by_id.return_value = None
        svc = _make_service(users=users, identities=AsyncMock())
        with pytest.raises(InvalidCredentials):
            await svc.unlink_google(user_id=uuid.uuid4(), remote_ip="1.2.3.4")
