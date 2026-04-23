"""Identity domain errors. Translated by the router layer to RFC 7807."""

from __future__ import annotations


class IdentityError(Exception):
    """Base class — carries a stable `code` the router maps to a Problem type."""

    code: str = "identity.generic"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class EmailAlreadyRegistered(IdentityError):
    code = "identity.email_taken"


class EmailDomainDenied(IdentityError):
    code = "auth/domain-denied"


class PasswordPolicyViolation(IdentityError):
    code = "auth/password-weak"


class InvalidCredentials(IdentityError):
    code = "auth/invalid-credentials"


class AccountNotVerified(IdentityError):
    code = "auth/email-unverified"


class AccountBanned(IdentityError):
    code = "auth/banned"


class AccountDeleted(IdentityError):
    code = "auth/deleted"


class Lockout(IdentityError):
    code = "auth/lockout"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"locked out for {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class CaptchaRequired(IdentityError):
    code = "auth/captcha-required"


class TokenInvalid(IdentityError):
    code = "auth/token-invalid"


class TokenExpired(IdentityError):
    code = "auth/token-expired"


class OriginalCreatorSelfDeleteBlocked(IdentityError):
    code = "tenancy/original-creator-self-delete-blocked"

    def __init__(self, blocked_orgs: list[str]) -> None:
        super().__init__(f"Original Creator of {len(blocked_orgs)} org(s)")
        self.blocked_orgs = blocked_orgs


__all__ = [
    "AccountBanned",
    "AccountDeleted",
    "AccountNotVerified",
    "CaptchaRequired",
    "EmailAlreadyRegistered",
    "EmailDomainDenied",
    "IdentityError",
    "InvalidCredentials",
    "Lockout",
    "OriginalCreatorSelfDeleteBlocked",
    "PasswordPolicyViolation",
    "TokenExpired",
    "TokenInvalid",
]
