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

    def __init__(self, *, provider: ApiKeyProvider, required: ProviderCapability) -> None:
        super().__init__(f"provider {provider.value!r} does not support capability {required.value!r}")
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


class KeyProjectScopeError(KeysError):
    """A pinned-key call named a key not carried into the caller's project (R7.04).

    Distinct from ``KeyNotFound``/``CapabilityMismatch`` so the pinned-key callers
    (RAG embed/rerank) can recognise a *scope* failure and degrade gracefully
    rather than failing the whole turn.
    """

    code = "keys/project-scope"

    def __init__(self, *, key_id: uuid.UUID, project_id: uuid.UUID) -> None:
        super().__init__(f"key {key_id} is not carried into project {project_id}")
        self.key_id = key_id
        self.project_id = project_id


class KeyGroupExhausted(KeysError):
    """Router ran through every member and none is usable (R7.08)."""

    code = "keys/group-exhausted"

    def __init__(self, *, group_id: uuid.UUID, reason: str, provider_detail: str | None = None) -> None:
        # `reason` stays a closed vocabulary: it becomes the `provider_exhausted:*`
        # error kind on the WS payload, which the frontend maps to copy. The
        # provider's own already-scrubbed refusal rides alongside it in the
        # message and in `provider_detail`, so a log line or an audit row can
        # name the actual cause without widening that vocabulary.
        super().__init__(f"group {group_id}: {reason}" + (f" ({provider_detail})" if provider_detail else ""))
        self.group_id = group_id
        self.reason = reason
        self.provider_detail = provider_detail


class UsageQuotaExceeded(KeysError):
    """Specific member is quota-capped right now (R7.09)."""

    code = "keys/usage-quota-exceeded"


class InvalidProviderConfig(KeysError):
    """Config validation failed for a provider that requires it (R7.16)."""

    code = "keys/invalid-provider-config"

    def __init__(self, *, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SearchActivationConflict(KeysError):
    """Two writers raced to activate a search key for the same project (§12.4)."""

    code = "search/activation-conflict"


class GroupWrongProject(KeysError):
    """Caller tried to attach a key not carried into this group's project."""

    code = "keys/not-carried"


class GroupMemberConflict(KeysError):
    """Key is already a member of this group or a priority collision occurred."""

    code = "keys/member-conflict"


__all__ = [
    "CapabilityMismatch",
    "GroupMemberConflict",
    "GroupWrongProject",
    "InvalidProviderConfig",
    "KeyGroupExhausted",
    "KeyNotFound",
    "KeyNotOwnedByCaller",
    "KeyProjectScopeError",
    "KeyRevoked",
    "KeysError",
    "ProviderUnauthorized",
    "SearchActivationConflict",
    "UsageQuotaExceeded",
]
