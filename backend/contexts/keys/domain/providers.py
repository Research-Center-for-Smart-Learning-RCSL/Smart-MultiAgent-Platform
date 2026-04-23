"""Provider enum + capability matrix (D.2, R7.01).

Single source of truth for:

- Which external providers SMAP understands for *LLM-style* keys.
- Which capability each provider offers (`llm_chat` / `embedding` / `rerank`).
- The validator that enforces capability fit at attach time (e.g. Key Group
  accepts only `llm_chat`-capable keys; RAG embedding config only accepts
  `embedding`-capable).

The DB enum is declared in the 0005 Alembic migration with matching label
strings. The authoritative capability table is here, not in the DB, because
capability membership is a product decision that can change per release
(enum members cannot easily shrink without a migration, but membership
semantics can adjust freely in code).

SoC: pure domain. No SQLAlchemy, no FastAPI, no external SDKs. The enum is
a `str, Enum` so it doubles as the DB label without a second mapping.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final


class ApiKeyProvider(str, enum.Enum):
    """Accepted providers for `api_keys.provider` (R7.01)."""

    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"
    VOYAGE = "voyage"
    COHERE = "cohere"


class ProviderCapability(str, enum.Enum):
    """What shape of request a key can sign (R7.01)."""

    LLM_CHAT = "llm_chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


# R7.01 — authoritative. Changing this table means reviewing D.∞ gate item 1.
_CAPABILITIES: Final[dict[ApiKeyProvider, frozenset[ProviderCapability]]] = {
    ApiKeyProvider.CLAUDE: frozenset({ProviderCapability.LLM_CHAT}),
    ApiKeyProvider.OPENAI: frozenset({ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}),
    ApiKeyProvider.GEMINI: frozenset({ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}),
    ApiKeyProvider.VOYAGE: frozenset({ProviderCapability.EMBEDDING}),
    ApiKeyProvider.COHERE: frozenset({ProviderCapability.RERANK}),
}


def capabilities_of(provider: ApiKeyProvider) -> frozenset[ProviderCapability]:
    """Return the capability set for `provider`."""
    return _CAPABILITIES[provider]


def supports(provider: ApiKeyProvider, capability: ProviderCapability) -> bool:
    """True if `provider` can service requests of shape `capability`."""
    return capability in _CAPABILITIES[provider]


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """The one capability a consumer slot needs.

    Key Group → `LLM_CHAT`. RAG embedding config → `EMBEDDING`.
    RAG rerank config → `RERANK`. Declared where the slot lives, not here.
    """

    capability: ProviderCapability


def assert_capability(
    provider: ApiKeyProvider, requirement: CapabilityRequirement
) -> None:
    """Raise `CapabilityMismatch` if `provider` cannot fulfil `requirement`.

    The caller catches this at the HTTP edge and maps it to
    `https://smap.local/problems/keys/capability-mismatch` (422).
    """
    if not supports(provider, requirement.capability):
        from contexts.keys.domain.errors import CapabilityMismatch

        raise CapabilityMismatch(provider=provider, required=requirement.capability)


__all__ = [
    "ApiKeyProvider",
    "CapabilityRequirement",
    "ProviderCapability",
    "assert_capability",
    "capabilities_of",
    "supports",
]
