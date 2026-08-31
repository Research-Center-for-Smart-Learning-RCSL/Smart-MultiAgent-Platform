"""Identity domain errors → RFC 7807 registration.

Lives in `interfaces/` so the layering stays clean: shared_kernel does not
know about any context, and routers do not import domain classes directly.
`app.main` calls `register(app)` at startup.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3) —
this module only owns the identity-specific map and extras.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.identity.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, make_context_handler

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
    errors.OAuthUnavailable: ("auth/oauth-unavailable", 503, "Google sign-in unavailable"),
    errors.OAuthExchangeFailed: ("auth/oauth-failed", 400, "Google sign-in could not be completed"),
    errors.GoogleEmailUnverified: ("auth/oauth-email-unverified", 403, "Google email not verified"),
    errors.OAuthIdentityConflict: ("auth/oauth-identity-conflict", 409, "Google account already linked"),
    errors.LastCredentialError: ("auth/last-credential", 409, "Set a password before unlinking"),
    errors.ActivationLinkRateLimited: (
        "admin/activation-links-rate-limited",
        429,
        "Too many activation-link requests for this account",
    ),
    errors.InvalidEmailDomain: ("admin/email-domain-invalid", 422, "Invalid email domain"),
    errors.EmailDomainPolicyVersionMismatch: (
        "admin/email-domain-policy-stale",
        409,
        "Email-domain policy changed since it was loaded",
    ),
    errors.EmailDomainPolicyRolloutFenced: (
        "admin/email-domain-policy-fenced",
        409,
        "Email-domain policy is read-only during this rollout phase",
    ),
    errors.EmailDomainPolicyUnavailable: (
        "admin/email-domain-policy-unavailable",
        503,
        "Email-domain policy is temporarily unavailable",
    ),
    errors.AdminProvisioningRateLimited: (
        "admin/provisioning-rate-limited",
        429,
        "Too many accounts created by this administrator",
    ),
    errors.OriginalCreatorSelfDeleteBlocked: (
        "tenancy/original-creator-self-delete-blocked",
        409,
        "Self-delete blocked — Original Creator transfer required",
    ),
}


def _extras(exc: Exception) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if isinstance(
        exc,
        errors.Lockout | errors.ActivationLinkRateLimited | errors.AdminProvisioningRateLimited,
    ):
        extras["retry_after_seconds"] = exc.retry_after_seconds
    if isinstance(exc, errors.EmailDomainPolicyRolloutFenced):
        # The phase itself, so the Admin UI can say *why* the form is read-only
        # and which operator step lifts it, rather than showing a bare conflict.
        extras["rollout_state"] = exc.rollout_state
    if isinstance(exc, errors.OriginalCreatorSelfDeleteBlocked):
        extras["blocked_org_ids"] = exc.blocked_orgs
    return extras


# One rendering function, built once. Used both to register the global handler
# and by routes that must emit the *same* RFC-7807 body while attaching a side
# effect (e.g. clearing a now-dead refresh cookie) — so the mapping has a single
# source of truth and routes never re-derive slugs/statuses.
_render = make_context_handler(_MAP, _extras)


#: Identity errors that mean a presented refresh token is permanently unusable
#: (unknown, expired, idle-timed-out, reused, or its owner no longer active).
#: The refresh route catches these to clear the inert cookie. Exposed from the
#: interfaces layer so routers never import domain error classes directly.
DEAD_REFRESH_ERRORS: tuple[type[errors.IdentityError], ...] = (
    errors.TokenExpired,
    errors.TokenInvalid,
)

#: OAuth-start failure the browser-navigation route (``/google/authorize``)
#: catches to redirect to the login page with an error, rather than emitting a
#: JSON 503. Exposed here so the router never imports domain error classes.
OAUTH_UNAVAILABLE_ERRORS: tuple[type[errors.IdentityError], ...] = (errors.OAuthUnavailable,)


async def render_problem(request: Request, exc: Exception) -> JSONResponse:
    """Render an identity domain error exactly as the global handler would.

    Lets a route return the canonical Problem response on a response object it
    controls (to clear a cookie) instead of letting the error bubble to the
    global handler, which builds its own response and would drop the cookie.
    """
    # `make_context_handler` widens its return to Any; pin it back to JSONResponse.
    response: JSONResponse = await _render(request, exc)
    return response


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.IdentityError, _render)


__all__ = ["DEAD_REFRESH_ERRORS", "OAUTH_UNAVAILABLE_ERRORS", "register", "render_problem"]
