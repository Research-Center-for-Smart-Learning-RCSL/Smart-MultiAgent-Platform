"""Keys-context domain errors (D.2 / §7).

Every error carries a `code` the `interfaces/error_mapping.py` module
translates into an RFC 7807 Problem. New slugs must also be registered in
`docs/operations.md` problem catalog (cross-cutting checklist §5).
"""

from __future__ import annotations

import uuid

from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability


class KeysError(Exception):
    """Base — translated by `contexts.keys.interfaces.error_mapping`."""

    code: str = "keys.generic"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class CapabilityMismatch(KeysError):
    """A key was offered into a slot its provider cannot service (R7.01, §7.4)."""

    code = "keys/capability-mismatch"

    def __init__(
        self, *, provider: ApiKeyProvider, required: ProviderCapability
    ) -> None:
        super().__init__(
            f"provider {provider.value!r} does not support capability {required.value!r}"
        )
        self.provider = provider
        self.required = required


class KeyNotFound(KeysError):
    code = "keys/not-found"


class KeyNotOwnedByCaller(KeysError):
    """Caller tried to mutate a key they do not own (R7.03–R7.05)."""

    code = "keys/not-owned"


class KeyRevoked(KeysError):
    """A call attempted to use a key that was just deleted / withdrawn (R7.04)."""

    code = "keys/revoked"


class ProviderUnauthorized(KeysError):
    """Live-validation probe rejected the supplied secret (R7.02)."""

    code = "keys/provider-unauthorized"

    def __init__(self, *, provider: ApiKeyProvider, detail: str) -> None:
        super().__init__(f"{provider.value}: {detail}")
        self.provider = provider
        self.detail = detail


class KeyGroupExhausted(KeysError):
    """Router ran through every member and none is usable (R7.08)."""

    code = "keys/group-exhausted"

    def __init__(self, *, group_id: uuid.UUID, reason: str) -> None:
        super().__init__(f"group {group_id}: {reason}")
        self.group_id = group_id
        self.reason = reason


class UsageQuotaExceeded(KeysError):
    """Specific member is quota-capped right now (R7.09)."""

    code = "keys/usage-quota-exceeded"


class SearchActivationConflict(KeysError):
    """Two writers raced to activate a search key for the same project (§12.4)."""

    code = "search/activation-conflict"


__all__ = [
    "CapabilityMismatch",
    "KeyGroupExhausted",
    "KeyNotFound",
    "KeyNotOwnedByCaller",
    "KeyRevoked",
    "KeysError",
    "ProviderUnauthorized",
    "SearchActivationConflict",
    "UsageQuotaExceeded",
]
