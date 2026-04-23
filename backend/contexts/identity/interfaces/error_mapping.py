"""Identity domain errors → RFC 7807 registration.

Lives in `interfaces/` so the layering stays clean: shared_kernel does not
know about any context, and routers do not import domain classes directly.
`app.main` calls `register(app)` at startup.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.identity.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

# (ExceptionClass) → (slug, http-status, title)
_MAP: dict[type[errors.IdentityError], tuple[str, int, str]] = {
    errors.EmailAlreadyRegistered: ("auth/email-taken", 409, "Email already registered"),
    errors.EmailDomainDenied: ("auth/domain-denied", 422, "Email domain rejected"),
    errors.InvalidEmailFormat: ("auth/email-invalid", 422, "Invalid email address"),
    errors.PasswordPolicyViolation: ("auth/password-weak", 422, "Password policy violation"),
    errors.InvalidCredentials: ("auth/invalid-credentials", 401, "Invalid credentials"),
    errors.AccountNotVerified: ("auth/email-unverified", 403, "Email not verified"),
    errors.AccountBanned: ("auth/banned", 403, "Account banned"),
    errors.AccountDeleted: ("auth/deleted", 410, "Account deleted"),
    errors.Lockout: ("auth/lockout", 429, "Too many failed attempts"),
    errors.CaptchaRequired: ("auth/captcha-required", 400, "CAPTCHA required"),
    errors.TokenInvalid: ("auth/token-invalid", 400, "Token invalid"),
    errors.TokenExpired: ("auth/token-expired", 401, "Token expired"),
    errors.OriginalCreatorSelfDeleteBlocked: (
        "tenancy/original-creator-self-delete-blocked", 409,
        "Self-delete blocked — Original Creator transfer required",
    ),
}


async def _handler(request: Request, exc: errors.IdentityError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc), ("auth/invalid-credentials", 400, "Request rejected"),
    )
    extras: dict[str, Any] = {}
    if isinstance(exc, errors.Lockout):
        extras["retry_after_seconds"] = exc.retry_after_seconds
    if isinstance(exc, errors.OriginalCreatorSelfDeleteBlocked):
        extras["blocked_org_ids"] = exc.blocked_orgs
    problem = Problem(
        type=problem_type(slug), title=title, status=status,
        detail=str(exc), extras=extras,
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.IdentityError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
