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


class InvalidEmailFormat(IdentityError):
    code = "auth/email-invalid"


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


class OAuthUnavailable(IdentityError):
    """Google login is not configured, or Google's endpoints were unreachable.
    Fail closed — the caller maps this to a 503-style error, never a 500."""

    code = "auth/oauth-unavailable"


class OAuthExchangeFailed(IdentityError):
    """The OAuth callback could not be completed: bad/expired state, or an
    id_token that failed signature/aud/iss/exp/nonce verification. → 400."""

    code = "auth/oauth-failed"


class GoogleEmailUnverified(IdentityError):
    """Google reported the account's email as unverified; provisioning/linking
    on it would reintroduce the takeover vector, so it is rejected."""

    code = "auth/oauth-email-unverified"


class OAuthIdentityConflict(IdentityError):
    """The Google account is already linked to a different SMAP user. → 409."""

    code = "auth/oauth-identity-conflict"


class LastCredentialError(IdentityError):
    """Refused to unlink the account's only remaining credential (no password and
    no other linked identity) until a password is set. → 409."""

    code = "auth/last-credential"


class ActivationLinkRateLimited(IdentityError):
    """Too many activation-link mints for one provisioned account (R6.18). → 429.

    Unlike the anti-enumeration limits on register/password-reset, this one is
    reported rather than silently swallowed: the caller is an authenticated Admin
    who needs to know the link was not re-issued, and there is no existence fact
    to hide from them.
    """

    code = "admin/activation-links-rate-limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"activation links rate-limited for {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class InvalidEmailDomain(IdentityError):
    """A policy entry, or an address's domain part, is not a bare domain (R19a.13).

    → 422 on the Admin write path. Carries the offending value in its message for
    the operator; the value is a domain the operator just typed, never an address.
    """

    code = "admin/email-domain-invalid"


class EmailDomainPolicyVersionMismatch(IdentityError):
    """A policy replacement carried a version the stored row no longer has. → 409.

    The Admin's form was built against a policy someone has since changed;
    blind-overwriting would silently discard the other edit.
    """

    code = "admin/email-domain-policy-stale"


class EmailDomainPolicyRolloutFenced(IdentityError):
    """A policy write was attempted outside the ``active`` rollout phase. → 409.

    Distinct from a stale version: nothing about the caller's request is wrong,
    and retrying with a fresher version will not help. Only an operator
    transition lifts this.
    """

    code = "admin/email-domain-policy-fenced"

    def __init__(self, rollout_state: str) -> None:
        super().__init__(f"email-domain policy writes are fenced in {rollout_state!r}")
        self.rollout_state = rollout_state


class EmailDomainPolicyUnavailable(IdentityError):
    """No authority for the email-domain policy could be reached. → 503.

    Raised rather than degrading to "no restriction": an unavailable authority is
    the exact condition under which the legacy Redis-only reader silently
    reopened registration (the source dossier's FU-11).
    """

    code = "admin/email-domain-policy-unavailable"


class InvalidLegacyEmailDomainPolicy(IdentityError):
    """The legacy Redis triple is in a shape that cannot be imported (Q-10).

    Fatal at boot by design. An invalid mode, a wrong key type, an invalid member
    or a mode absent while a list holds members are all distinguishable
    corruption, and importing a guess would make it authoritative.
    """

    code = "admin/email-domain-legacy-invalid"


class AdminProvisioningRateLimited(IdentityError):
    """One Admin exceeded the rolling account-creation cap (R6.18). → 429.

    Bounds request-speed abuse from an Admin session that has already been lost;
    the bucket is keyed by the authenticated actor, never by anything the request
    supplies. Reported rather than swallowed for the same reason as
    :class:`ActivationLinkRateLimited`: the caller is an authenticated Admin with
    no existence fact to hide from them.
    """

    code = "admin/provisioning-rate-limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"admin provisioning rate-limited for {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class OriginalCreatorSelfDeleteBlocked(IdentityError):
    code = "tenancy/original-creator-self-delete-blocked"

    def __init__(self, blocked_orgs: list[str]) -> None:
        super().__init__(f"Original Creator of {len(blocked_orgs)} org(s)")
        self.blocked_orgs = blocked_orgs


__all__ = [
    "AccountBanned",
    "AccountDeleted",
    "AccountNotVerified",
    "ActivationLinkRateLimited",
    "AdminProvisioningRateLimited",
    "CaptchaRequired",
    "EmailAlreadyRegistered",
    "EmailDomainDenied",
    "EmailDomainPolicyRolloutFenced",
    "EmailDomainPolicyUnavailable",
    "EmailDomainPolicyVersionMismatch",
    "GoogleEmailUnverified",
    "IdentityError",
    "InvalidCredentials",
    "InvalidEmailDomain",
    "InvalidEmailFormat",
    "InvalidLegacyEmailDomainPolicy",
    "LastCredentialError",
    "Lockout",
    "OAuthExchangeFailed",
    "OAuthIdentityConflict",
    "OAuthUnavailable",
    "OriginalCreatorSelfDeleteBlocked",
    "PasswordPolicyViolation",
    "TokenExpired",
    "TokenInvalid",
]
