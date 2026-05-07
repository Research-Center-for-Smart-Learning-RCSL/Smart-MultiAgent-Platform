"""Access-token decode + jti denylist + ban check → Principal.

Populates `request.state.auth_ctx.principal` for anonymous public endpoints
the caller is optional; AuthZ is enforced by per-route `require(...)` deps.

Public routes (R19.01) explicitly call `Depends(allow_anon)` instead — the
middleware is non-fatal: missing / malformed / expired tokens leave the ctx
unauthenticated; only an *affirmatively denied* token (ban / denylisted jti)
returns 401 immediately.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth import jwt, tokens
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.errors.problem import Problem, problem_type

# Status strings mirrored from `contexts.identity.domain.models.UserStatus`.
# Kept as a constant here so the middleware does not import the domain layer
# (import-linter rule 3). Any future value added to UserStatus should be
# reflected here.
_STATUS_ACTIVE = "active"
_STATUS_BANNED = "banned"
_STATUS_DELETED = "deleted"

# Paths that return presigned download URLs — blocked even on GET when impersonating,
# to prevent an admin from exfiltrating another user's files via an impersonation JWT.
# Deny-list patterns: any GET whose path matches one of these segments is blocked,
# so a newly-added /download or /export endpoint is gated by default and an
# operator must explicitly route it differently to bypass.
_IMPERSONATION_DOWNLOAD_SEGMENTS = (
    "/download",
    "/export",
    "/exports",
    "/presigned",
    "/attachments",
)


def _is_impersonation_blocked(path: str) -> bool:
    # Match path segments only — `/api/orgs/{id}/exports`, `/api/exports/{id}`,
    # `/api/.../files/download`, etc. all match; `/api/exporters` does NOT.
    parts = path.split("/")
    return any(seg.lstrip("/") in parts for seg in _IMPERSONATION_DOWNLOAD_SEGMENTS)


_PROBLEM_MEDIA = "application/problem+json"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        ctx: RequestContext = getattr(request.state, "auth_ctx", None) or RequestContext()
        request.state.auth_ctx = ctx

        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return await call_next(request)
        token = header.split(" ", 1)[1].strip()
        if not token:
            return await call_next(request)

        try:
            claims = jwt.verify_access_token(token)
        except jwt.JwtError as exc:
            return _deny("auth/token-expired", "Access token invalid or expired", 401, request, str(exc))

        if await tokens.is_denied(claims.jti):
            return _deny("auth/token-revoked", "Access token revoked", 401, request, "jti is denylisted")

        sm = get_sessionmaker()
        async with sm() as session:
            profile = await IdentityFacade(session).get_profile(claims.sub)
            if profile is None or profile.status.value == _STATUS_DELETED:
                return _deny("auth/invalid-credentials", "Account not found", 401, request, "user missing")
            if profile.status.value == _STATUS_BANNED:
                return _deny("auth/banned", "Account banned", 403, request, "banned")

        ctx.principal = Principal(
            user_id=profile.id,
            is_admin=profile.is_admin,
            email_verified=profile.email_verified,
        )
        ctx.session_id = claims.session_id
        ctx.access_jti = claims.jti
        ctx.access_exp = claims.exp
        ctx.impersonated_by = claims.impersonated_by

        if claims.impersonated_by is not None:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return _deny(
                    "admin/impersonation-read-only",
                    "Impersonation sessions are read-only",
                    403,
                    request,
                    "non-GET request via impersonation JWT",
                )
            if _is_impersonation_blocked(request.url.path):
                return _deny(
                    "admin/impersonation-read-only",
                    "Impersonation sessions cannot access download endpoints",
                    403,
                    request,
                    "data-export path accessed via impersonation JWT",
                )

        return await call_next(request)


def _deny(slug: str, title: str, status: int, request: Request, detail: str) -> Response:
    problem = Problem(
        type=problem_type(slug),
        title=title,
        status=status,
        detail=detail,
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=_PROBLEM_MEDIA,
    )
