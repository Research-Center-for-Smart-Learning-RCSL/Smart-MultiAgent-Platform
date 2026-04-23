"""Canonical at-rest envelope-encryption surface (D.1, R7.06, R7.14–R7.15).

Three responsibilities, nothing else:

1. **AAD builders.** `api_key_aad(uuid)` / `search_key_aad(uuid)` produce the
   bytes to bind a DEK to a single logical row, so a ciphertext stolen from
   `api_keys` cannot be replayed against `search_keys` even if both share a
   transit master key (R7.06 step 3 — "AAD bound to logical identity").

2. **Seal / unseal helpers.** Wrap `VaultClient.encrypt_envelope` and
   `decrypt_envelope` with a tiny, framework-free API that callers (services,
   rotation CLI, tests) hit. Version fields on the returned `EnvelopeRecord`
   are ready for D.10 rewrap.

3. **Rewrap.** `rewrap_envelope(record)` re-wraps the DEK against the current
   Transit version. Used by D.10. Plaintext DEK never crosses the Python
   boundary during rewrap — Vault performs the unwrap/wrap atomically.

SoC:

- This module imports from `shared_kernel.infra.vault` only. It never imports
  `contexts.*`, never imports FastAPI, never writes to the DB.
- The `VaultClient` singleton lookup happens via
  `shared_kernel.auth.clients.get_vault_client` so tests can monkey-patch one
  seam instead of threading a client through every service.
"""

from __future__ import annotations

import uuid
from typing import Final

from shared_kernel.infra.vault import EnvelopeRecord, _parse_transit_version

# AAD namespace constants — the plaintext prefix used to build the AAD bytes
# for each storage table. Changing one of these is a wire-compatibility break
# (old rows stop decrypting), so they live as module constants and get asserted
# against in a golden test.
ENVELOPE_AAD_NS_API_KEYS: Final = "api_keys"
ENVELOPE_AAD_NS_SEARCH_KEYS: Final = "search_keys"


def api_key_aad(key_id: uuid.UUID) -> bytes:
    """Build the AAD for an `api_keys` row."""
    return f"{ENVELOPE_AAD_NS_API_KEYS}:{key_id}".encode("ascii")


def search_key_aad(key_id: uuid.UUID) -> bytes:
    """Build the AAD for a `search_keys` row."""
    return f"{ENVELOPE_AAD_NS_SEARCH_KEYS}:{key_id}".encode("ascii")


def encrypt_envelope(plaintext: bytes, aad: bytes) -> EnvelopeRecord:
    """Seal `plaintext` under a fresh per-row DEK.

    Callers must pass the AAD built from one of the `*_aad` helpers above;
    passing bare strings would silently bypass the cross-table binding.
    """
    from shared_kernel.auth.clients import get_vault_client  # local import: cycle-safe

    return get_vault_client().encrypt_envelope(plaintext, aad)


def decrypt_envelope(record: EnvelopeRecord, aad: bytes) -> bytes:
    """Unseal `record` under the same AAD the writer used.

    Mismatch is a hard error (HMAC verification fails before the DEK unwrap
    so a bad AAD never reaches Transit).
    """
    from shared_kernel.auth.clients import get_vault_client

    return get_vault_client().decrypt_envelope(record, aad)


def rewrap_envelope(record: EnvelopeRecord) -> EnvelopeRecord:
    """Re-wrap `record.dek_wrapped` against the current Transit version.

    Returns a new `EnvelopeRecord` with updated `dek_wrapped` +
    `transit_key_version`. Ciphertext, nonce, HMAC, and `hmac_key_version`
    are unchanged — the symmetric payload is not re-encrypted, only the DEK
    wrap is refreshed. This is what D.10 iterates over every row.
    """
    from shared_kernel.auth.clients import get_vault_client

    new_wrapped = get_vault_client().rewrap_dek(record.dek_wrapped)
    return EnvelopeRecord(
        ciphertext=record.ciphertext,
        nonce=record.nonce,
        dek_wrapped=new_wrapped,
        ciphertext_hmac=record.ciphertext_hmac,
        transit_key_version=_parse_transit_version(new_wrapped),
        hmac_key_version=record.hmac_key_version,
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
