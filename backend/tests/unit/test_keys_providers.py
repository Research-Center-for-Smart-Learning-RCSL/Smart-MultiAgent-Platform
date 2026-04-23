"""D.2 — R7.01 provider/capability table is the exact shape documented.

This test is the goldens check for §D.∞ gate item 1 ("cohere capability row
matches R7.01 exactly"). Changing the table without changing this test is a
review red flag.
"""

from __future__ import annotations

import pytest

from contexts.keys.domain.errors import CapabilityMismatch
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
    capabilities_of,
    supports,
)


def test_capability_table_matches_r7_01() -> None:
    expected: dict[ApiKeyProvider, frozenset[ProviderCapability]] = {
        ApiKeyProvider.CLAUDE: frozenset({ProviderCapability.LLM_CHAT}),
        ApiKeyProvider.OPENAI: frozenset(
            {ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}
        ),
        ApiKeyProvider.GEMINI: frozenset(
            {ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}
        ),
        ApiKeyProvider.VOYAGE: frozenset({ProviderCapability.EMBEDDING}),
        ApiKeyProvider.COHERE: frozenset({ProviderCapability.RERANK}),
    }
    for provider, caps in expected.items():
        assert capabilities_of(provider) == caps


def test_cohere_rerank_only() -> None:
    assert supports(ApiKeyProvider.COHERE, ProviderCapability.RERANK)
    assert not supports(ApiKeyProvider.COHERE, ProviderCapability.LLM_CHAT)
    assert not supports(ApiKeyProvider.COHERE, ProviderCapability.EMBEDDING)


def test_voyage_embedding_only() -> None:
    assert supports(ApiKeyProvider.VOYAGE, ProviderCapability.EMBEDDING)
    assert not supports(ApiKeyProvider.VOYAGE, ProviderCapability.LLM_CHAT)
    assert not supports(ApiKeyProvider.VOYAGE, ProviderCapability.RERANK)


def test_assert_capability_accepts_fit() -> None:
    assert_capability(
        ApiKeyProvider.OPENAI,
        CapabilityRequirement(capability=ProviderCapability.EMBEDDING),
    )


def test_assert_capability_rejects_mismatch() -> None:
    with pytest.raises(CapabilityMismatch) as excinfo:
        assert_capability(
            ApiKeyProvider.CLAUDE,
            CapabilityRequirement(capability=ProviderCapability.EMBEDDING),
        )
    err = excinfo.value
    assert err.provider is ApiKeyProvider.CLAUDE
    assert err.required is ProviderCapability.EMBEDDING
    assert err.code == "keys/capability-mismatch"


def test_error_codes_are_stable() -> None:
    from contexts.keys.domain import errors

    # Consumers in operations.md reference these slugs — changing one is a
    # breaking change for the error catalog.
    assert errors.CapabilityMismatch.code == "keys/capability-mismatch"
    assert errors.KeyRevoked.code == "keys/revoked"
    assert errors.KeyGroupExhausted.code == "keys/group-exhausted"
    assert errors.ProviderUnauthorized.code == "keys/provider-unauthorized"
    assert errors.UsageQuotaExceeded.code == "keys/usage-quota-exceeded"
    assert errors.SearchActivationConflict.code == "search/activation-conflict"
