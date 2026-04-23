"""Keys domain errors → RFC 7807 problems.

Mirrors `contexts/identity/interfaces/error_mapping.py`. `app.main` calls
`register(app)` at startup so every `KeysError` raised from an endpoint or
service becomes a `application/problem+json` response automatically.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.keys.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.KeysError], tuple[str, int, str]] = {
    errors.CapabilityMismatch: (
        "keys/capability-mismatch", 422, "Key capability does not match slot",
    ),
    errors.KeyNotFound: ("keys/not-found", 404, "Key not found"),
    errors.KeyNotOwnedByCaller: ("keys/not-owned", 403, "Key not owned by caller"),
    errors.KeyRevoked: ("keys/revoked", 410, "Key revoked"),
    errors.ProviderUnauthorized: (
        "keys/provider-unauthorized", 422, "Provider rejected the supplied secret",
    ),
    errors.KeyGroupExhausted: (
        "keys/group-exhausted", 503, "Key group exhausted",
    ),
    errors.UsageQuotaExceeded: (
        "keys/usage-quota-exceeded", 429, "Hourly usage quota exceeded",
    ),
    errors.SearchActivationConflict: (
        "search/activation-conflict", 409, "Search key activation race",
    ),
}


async def _handler(request: Request, exc: errors.KeysError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc), ("keys/not-found", 400, "Request rejected"),
    )
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
    problem = Problem(
        type=problem_type(slug), title=title, status=status,
        detail=str(exc), extras=extras,
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.KeysError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
