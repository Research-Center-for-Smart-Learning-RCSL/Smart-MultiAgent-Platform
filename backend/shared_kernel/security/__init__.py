"""Cross-cutting security primitives (D.1).

This package exists so that context code (`contexts.keys`, `contexts.knowledge`,
the `smap.rotation` CLI, etc.) never imports `shared_kernel.infra.vault`
directly for at-rest encryption.

Why the indirection: `shared_kernel.infra.vault` is the transport adapter —
it knows Transit, KV, AppRole. The application-facing contract (envelope
record + AAD builders + seal/unseal) lives here so the Vault module can
change its internals without rippling into every consumer.
"""

from __future__ import annotations

from shared_kernel.security.envelope import (
    ENVELOPE_AAD_NS_API_KEYS,
    ENVELOPE_AAD_NS_SEARCH_KEYS,
    EnvelopeRecord,
    api_key_aad,
    decrypt_envelope,
    encrypt_envelope,
    rewrap_envelope,
    search_key_aad,
)

__all__ = [
    "ENVELOPE_AAD_NS_API_KEYS",
    "ENVELOPE_AAD_NS_SEARCH_KEYS",
    "EnvelopeRecord",
    "api_key_aad",
    "decrypt_envelope",
    "encrypt_envelope",
    "rewrap_envelope",
    "search_key_aad",
]
