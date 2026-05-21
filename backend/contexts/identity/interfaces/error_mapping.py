"""Identity domain errors → RFC 7807 registration.

Lives in `interfaces/` so the layering stays clean: shared_kernel does not
know about any context, and routers do not import domain classes directly.
`app.main` calls `register(app)` at startup.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3) —
this module only owns the identity-specific map and extras.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from contexts.identity.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

# (ExceptionClass) → (slug, http-status, title)
_MAP: ErrorMap = {
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
        "tenancy/original-creator-self-delete-blocked",
        409,
        "Self-delete blocked — Original Creator transfer required",
    ),
}


def _extras(exc: Exception) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if isinstance(exc, errors.Lockout):
        extras["retry_after_seconds"] = exc.retry_after_seconds
    if isinstance(exc, errors.OriginalCreatorSelfDeleteBlocked):
        extras["blocked_org_ids"] = exc.blocked_orgs
    return extras


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.IdentityError, _MAP, _extras)


__all__ = ["register"]
