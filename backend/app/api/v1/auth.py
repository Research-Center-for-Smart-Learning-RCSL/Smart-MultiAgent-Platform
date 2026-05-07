"""`/api/auth/*` — user-facing auth endpoints (§22.1).

Every handler is intentionally thin: extract → call application service →
map result. Domain errors bubble up and are translated by
`shared_kernel.errors.domain_mapping`. No SQL lives here.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import HTTPException, Response, APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.application.auth_service import AuthService
from contexts.identity.application.factory import create_auth_service
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

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
    password: str


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
    current_password: str
    new_password: str = Field(min_length=10, max_length=1024)


class ChangeEmailIn(BaseModel):
    new_email: EmailStr
    password: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    status: str
    is_admin: bool


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: str
    last_used_at: str
    expires_at: str
    user_agent: str | None
    ip_inet: str | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = _service(db)
    user = await service.register(
        email=body.email,
        password=body.password,
        captcha_token=body.captcha_token,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return {"id": str(user.id), "status": user.status.value}


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailIn,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = _service(db)
    user = await service.verify_email(
        body.token, remote_ip=ctx.actor_ip, request_id=ctx.request_id,
    )
    return {"id": str(user.id), "status": user.status.value}


@router.get("/verify-email")
async def verify_email_via_link(
    token: str = Query(..., min_length=8),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    service = _service(db)
    user = await service.verify_email(
        token, remote_ip=ctx.actor_ip, request_id=ctx.request_id,
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
        remote_ip=ctx.actor_ip or (request.client.host if request.client else "0.0.0.0"),
        user_agent=request.headers.get("User-Agent"),
        request_id=ctx.request_id,
    )
    _set_refresh_cookie(response, outcome.tokens.refresh_token)
    return TokenPairOut(**outcome.tokens.__dict__)


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
        remote_ip=ctx.actor_ip or "0.0.0.0",
        request_id=ctx.request_id,
    )
    _set_refresh_cookie(response, pair.refresh_token)
    return TokenPairOut(**pair.__dict__)


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
    _clear_refresh_cookie(response)
    if refresh_token is None:
        return
    service = _service(db)
    ttl_seconds = get_settings().jwt.access_ttl_seconds
    if ctx.access_jti is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Missing access JTI in authenticated context")
    await service.logout(
        refresh_token=refresh_token,
        access_jti=ctx.access_jti,
        access_ttl=timedelta(seconds=ttl_seconds),
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@router.post("/request-password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequestIn,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = _service(db)
    await service.request_password_reset(
        email=body.email, remote_ip=ctx.actor_ip, request_id=ctx.request_id,
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
    )


@router.get("/sessions")
async def list_sessions(
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[SessionOut]:
    service = _service(db)
    sessions = await service.list_sessions(user_id=principal.user_id)
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


__all__ = ["router"]
