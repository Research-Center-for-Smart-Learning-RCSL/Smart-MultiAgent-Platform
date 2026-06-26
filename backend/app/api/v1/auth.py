"""`/api/auth/*` — user-facing auth endpoints (§22.1).

Every handler is intentionally thin: extract → call application service →
map result. Domain errors bubble up and are translated by
`shared_kernel.errors.domain_mapping`. No SQL lives here.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from app.config.settings import get_settings
from contexts.identity.application.auth_service import AuthService, TokenPair
from contexts.identity.application.factory import create_auth_service
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth import captcha, ratelimit, tokens
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.errors.problem import Problem, problem_type
from shared_kernel.realtime import mint_ws_ticket

router = APIRouter(prefix="/api/auth", tags=["auth"])

_REFRESH_COOKIE = "smap_refresh"
_REFRESH_COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        max_age=s.jwt.refresh_ttl_seconds,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=s.security.session_cookie_secure,
        samesite=s.security.session_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        path=_REFRESH_COOKIE_PATH,
        secure=s.security.session_cookie_secure,
        samesite=s.security.session_cookie_samesite,
    )


def _service(db: AsyncSession) -> AuthService:
    return create_auth_service(db, public_origin=_public_origin())


def _public_origin() -> str:
    # Single origin (§19a.07). Fall back to localhost in dev.
    origins = get_settings().security.cors_origins
    return origins[0] if origins else "http://localhost:8080"


# API-9: per-target-email cap on password-reset requests. The rate-limit
# middleware only buckets recovery flows by IP, so without this an attacker
# rotating IPs can flood reset emails at a chosen victim. Keyed on the
# *requested* address, so it leaks nothing about whether the account exists.
_RESET_EMAIL_WINDOW_SEC = 3600
_RESET_EMAIL_MAX = 5


def _too_many_reset_requests(decision: ratelimit.Decision) -> HTTPException:
    problem = Problem(
        type=problem_type("rate-limited"),
        title="Too Many Requests",
        status=429,
        detail="Password-reset requests for this address are temporarily rate-limited",
        extras={"retry_after_seconds": decision.retry_after_seconds},
    )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=problem.dump(),
        headers={
            "Retry-After": str(decision.retry_after_seconds),
            "Content-Type": "application/problem+json",
        },
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=1024)
    captcha_token: str | None = None


class VerifyEmailIn(BaseModel):
    token: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class RefreshIn(BaseModel):
    refresh_token: str | None = None


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=1024)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=10, max_length=1024)


class ChangeEmailIn(BaseModel):
    new_email: EmailStr
    password: str = Field(max_length=1024)


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=1024)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


def _token_pair_out(pair: TokenPair) -> TokenPairOut:
    # `TokenPair` is a `slots=True` dataclass and therefore has no `__dict__`;
    # splatting `**pair.__dict__` raises AttributeError. Map fields explicitly.
    return TokenPairOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        token_type=pair.token_type,
        expires_in=pair.expires_in,
    )


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    status: str
    is_admin: bool
    display_name: str | None = None


class UpdateProfileIn(BaseModel):
    # Optional, non-unique label. ``None`` (or blank) clears it. Server-side
    # normalisation strips control chars and re-trims; this is the outer bound.
    display_name: str | None = Field(default=None, max_length=50)


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: str
    last_used_at: str
    expires_at: str
    user_agent: str | None
    ip_inet: str | None


class WsTicketOut(BaseModel):
    ticket: str
    expires_in: int


class CaptchaConfigOut(BaseModel):
    mode: str  # "on" | "off"
    provider: str  # "hcaptcha" | "turnstile" | "off"
    sitekey: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: RegisterIn,
    request: Request,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = _service(db)
    # Uniform response regardless of whether the email is new or already taken
    # (SEC-M4): the service either creates a pending user + sends verification,
    # or notifies the existing owner out-of-band. The HTTP reply never reveals
    # which path ran, so registration cannot be used to enumerate accounts.
    await service.register(
        email=body.email,
        password=body.password,
        captcha_token=body.captcha_token,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"status": "verification_pending"}


@router.get("/captcha-config")
async def captcha_config() -> CaptchaConfigOut:
    """Public CAPTCHA config for the registration widget (R19a.12).

    Unauthenticated by design — the SPA fetches this before the user has any
    credentials. Returns only the public provider/sitekey/mode; the verify
    secret never leaves the backend. When ``mode=off`` the SPA renders no widget.
    """
    cfg = captcha.public_config()
    return CaptchaConfigOut(mode=cfg.mode, provider=cfg.provider, sitekey=cfg.sitekey)


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailIn,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = _service(db)
    user = await service.verify_email(
        body.token,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"id": str(user.id), "status": user.status.value}


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> TokenPairOut:
    service = _service(db)
    outcome = await service.login(
        email=body.email,
        password=body.password,
        remote_ip=ctx.actor_ip or (request.client.host if request.client else "0.0.0.0"),  # noqa: S104
        user_agent=request.headers.get("User-Agent"),
        request_id=ctx.request_id,
    )
    _set_refresh_cookie(response, outcome.tokens.refresh_token)
    return _token_pair_out(outcome.tokens)


@router.post("/refresh")
async def refresh(
    body: RefreshIn,
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> TokenPairOut:
    refresh_token = request.cookies.get(_REFRESH_COOKIE) or body.refresh_token
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")
    service = _service(db)
    pair = await service.refresh(
        refresh_token=refresh_token,
        remote_ip=ctx.actor_ip or "0.0.0.0",  # noqa: S104
        request_id=ctx.request_id,
    )
    _set_refresh_cookie(response, pair.refresh_token)
    return _token_pair_out(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(
    body: LogoutIn,
    request: Request,
    response: Response,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    refresh_token = request.cookies.get(_REFRESH_COOKIE) or body.refresh_token
    if ctx.access_jti is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Missing access JTI in authenticated context",
        )
    if refresh_token is None:
        await tokens.deny_access_jti(ctx.access_jti)
        _clear_refresh_cookie(response)
        return
    service = _service(db)
    ttl_seconds = get_settings().jwt.access_ttl_seconds
    await service.logout(
        refresh_token=refresh_token,
        access_jti=ctx.access_jti,
        access_ttl=timedelta(seconds=ttl_seconds),
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    _clear_refresh_cookie(response)


@router.post("/request-password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequestIn,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    # API-9: per-target-email limiter, on top of the per-IP recovery bucket.
    email_key = str(body.email).strip().lower()
    decision = await ratelimit.check_raw(
        key=f"rl:auth-recovery:reset-email:{email_key}",
        window_sec=_RESET_EMAIL_WINDOW_SEC,
        max_count=_RESET_EMAIL_MAX,
    )
    if not decision.allowed:
        raise _too_many_reset_requests(decision)
    service = _service(db)
    await service.request_password_reset(
        email=body.email,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def reset_password(
    body: PasswordResetIn,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = _service(db)
    await service.reset_password(
        token=body.token,
        new_password=body.new_password,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def change_password(
    body: ChangePasswordIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = _service(db)
    await service.change_password(
        user_id=principal.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/change-email", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def change_email(
    body: ChangeEmailIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = _service(db)
    await service.change_email(
        user_id=principal.user_id,
        new_email=body.new_email,
        password=body.password,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_account(
    body: DeleteAccountIn,
    response: Response,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    """Self-service account deletion (R6.07 / R8.14 / R8.18).

    Re-confirms the current password (destructive, recovery-gated action), soft-
    deletes the account + its tenancy footprint, then clears the refresh cookie.
    Returns 409 (``tenancy/original-creator-self-delete-blocked`` with
    ``blocked_org_ids``) when the caller is the Original Creator of an Org that
    still has other active members.
    """
    service = _service(db)
    await service.delete_account(
        user_id=principal.user_id,
        password=body.password,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    _clear_refresh_cookie(response)


@router.get("/me")
async def me(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> UserOut:
    facade = IdentityFacade(db)
    profile = await facade.get_profile(principal.user_id)
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not found for authenticated token")
    return UserOut(
        id=profile.id,
        email=profile.email,
        email_verified=profile.email_verified,
        status=profile.status.value,
        is_admin=profile.is_admin,
        display_name=profile.display_name,
    )


@router.patch("/me")
async def update_me(
    body: UpdateProfileIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> UserOut:
    service = _service(db)
    await service.update_display_name(
        user_id=principal.user_id,
        display_name=body.display_name,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    facade = IdentityFacade(db)
    profile = await facade.get_profile(principal.user_id)
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not found for authenticated token")
    return UserOut(
        id=profile.id,
        email=profile.email,
        email_verified=profile.email_verified,
        status=profile.status.value,
        is_admin=profile.is_admin,
        display_name=profile.display_name,
    )


@router.get("/sessions")
async def list_sessions(
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SessionOut]:
    service = _service(db)
    sessions = await service.list_sessions(
        user_id=principal.user_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at.isoformat(),
            last_used_at=s.last_used_at.isoformat(),
            expires_at=s.expires_at.isoformat(),
            user_agent=s.user_agent,
            ip_inet=s.ip_inet,
        )
        for s in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_session(
    session_id: uuid.UUID,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = _service(db)
    await service.revoke_session(
        user_id=principal.user_id,
        session_id=session_id,
        access_ttl=timedelta(seconds=get_settings().jwt.access_ttl_seconds),
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/ws-ticket")
async def issue_ws_ticket(
    request: Request,
    principal: Principal = Depends(current_principal),
) -> WsTicketOut:
    """Mint a short-lived, single-use ticket for a WebSocket handshake (FE-7).

    Browsers cannot set `Authorization` on a WS upgrade, so the credential
    must ride in `Sec-WebSocket-Protocol` — a header proxies and access logs
    record. Placing the JWT there leaks it; instead this endpoint (reached
    over HTTPS, where the bearer token sits in the redacted `Authorization`
    header) stashes the access token behind an opaque ticket the handshake
    redeems exactly once. A ticket later found in a log is already consumed.

    The `current_principal` dependency gates the call; the raw token is read
    straight off the header so the exact JWT the caller presented is what the
    WS handshake later verifies.
    """
    auth_header = request.headers.get("authorization", "")
    scheme, _, raw_token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    ticket, ttl = await mint_ws_ticket(raw_token)
    return WsTicketOut(ticket=ticket, expires_in=ttl)


__all__ = ["router"]
