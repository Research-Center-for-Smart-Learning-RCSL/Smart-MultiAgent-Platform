"""Keys domain errors → RFC 7807 problems.

`app.main` calls `register(app)` at startup so every `KeysError` raised from an
endpoint or service becomes an `application/problem+json` response. Dispatch +
fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from contexts.keys.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.CapabilityMismatch: (
        "keys/capability-mismatch",
        422,
        "Key capability does not match slot",
    ),
    errors.KeyNotFound: ("keys/not-found", 404, "Key not found"),
    errors.KeyNotOwnedByCaller: ("keys/not-owned", 403, "Key not owned by caller"),
    errors.KeyRevoked: ("keys/revoked", 410, "Key revoked"),
    errors.ProviderUnauthorized: (
        "keys/provider-unauthorized",
        422,
        "Provider rejected the supplied secret",
    ),
    errors.KeyGroupExhausted: (
        "keys/group-exhausted",
        503,
        "Key group exhausted",
    ),
    errors.UsageQuotaExceeded: (
        "keys/usage-quota-exceeded",
        429,
        "Hourly usage quota exceeded",
    ),
    errors.SearchActivationConflict: (
        "search/activation-conflict",
        409,
        "Search key activation race",
    ),
    errors.GroupWrongProject: (
        "keys/not-carried",
        422,
        "Key not carried into project",
    ),
    errors.GroupMemberConflict: (
        "keys/member-conflict",
        409,
        "Key already in group or priority conflict",
    ),
}


def _extras(exc: Exception) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if isinstance(exc, errors.CapabilityMismatch):
        extras["provider"] = exc.provider.value
        extras["required_capability"] = exc.required.value
    elif isinstance(exc, errors.ProviderUnauthorized):
        extras["provider"] = exc.provider.value
        extras["detail_from_provider"] = exc.detail
    elif isinstance(exc, errors.KeyGroupExhausted):
        extras["group_id"] = str(exc.group_id)
        extras["reason"] = exc.reason
    return extras


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.KeysError, _MAP, _extras)


__all__ = ["register"]
