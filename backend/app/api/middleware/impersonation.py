"""Impersonation policy enforcement middleware.

Extracted from AuthMiddleware so JWT verification + Principal creation
(identity concern) stays separate from impersonation access policy
(admin/security concern).

This middleware runs AFTER AuthMiddleware has populated
``request.state.auth_ctx``. It checks whether the request was made via
an impersonation JWT and, if so, enforces read-only + download-deny
policies.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared_kernel.errors.problem import Problem, problem_type

# Paths that return presigned download URLs -- blocked even on GET when impersonating,
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
    # Match path segments only -- `/api/orgs/{id}/exports`, `/api/exports/{id}`,
    # `/api/.../files/download`, etc. all match; `/api/exporters` does NOT.
    parts = path.split("/")
    return any(seg.lstrip("/") in parts for seg in _IMPERSONATION_DOWNLOAD_SEGMENTS)


_PROBLEM_MEDIA = "application/problem+json"


class ImpersonationPolicyMiddleware(BaseHTTPMiddleware):
    """Enforces read-only + download-deny for impersonation sessions."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        ctx = getattr(request.state, "auth_ctx", None)
        if ctx is None or ctx.impersonated_by is None:
            return await call_next(request)

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


__all__ = ["ImpersonationPolicyMiddleware"]
