"""Envelope encryption for agent tool auth material (A.6).

Generalizes the AAD namespace so both legacy MCP bindings (sealed before the
rename with ``mcp_binding_auth:<id>``) and new tool rows (sealed with
``agent_tool_auth:<id>``) decrypt correctly.

The ``aad_ns`` key is **plaintext metadata** in the sealed dict, read before
decryption to choose the AAD.  Old blobs (no marker) default to the legacy
namespace — the AAD is still authenticated by the envelope's HMAC, so a
tampered ``aad_ns`` fails decryption.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from shared_kernel.security import envelope as env

_LEGACY_AAD_NS = "mcp_binding_auth"
_NEW_AAD_NS = "agent_tool_auth"


def _build_aad(ns: str, tool_id: uuid.UUID) -> bytes:
    return ns.encode("ascii") + b":" + str(tool_id).encode("ascii")


def _record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "ciphertext": base64.b64encode(record.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(record.nonce).decode("ascii"),
        "dek_wrapped": record.dek_wrapped,
        "ciphertext_hmac": base64.b64encode(record.ciphertext_hmac).decode("ascii"),
        "transit_key_version": record.transit_key_version,
        "hmac_key_version": record.hmac_key_version,
    }


def _dict_to_record(sealed: dict[str, Any]) -> Any:
    from shared_kernel.infra.vault import EnvelopeRecord

    return EnvelopeRecord(
        ciphertext=base64.b64decode(sealed["ciphertext"]),
        nonce=base64.b64decode(sealed["nonce"]),
        dek_wrapped=sealed["dek_wrapped"],
        ciphertext_hmac=base64.b64decode(sealed["ciphertext_hmac"]),
        transit_key_version=int(sealed["transit_key_version"]),
        hmac_key_version=int(sealed["hmac_key_version"]),
    )


def seal_tool_auth(tool_id: uuid.UUID, auth: dict[str, Any]) -> dict[str, Any]:
    """Envelope-encrypt auth for a new tool row (uses ``agent_tool_auth`` AAD)."""
    plaintext = json.dumps(auth, sort_keys=True).encode("utf-8")
    record = env.encrypt_envelope(plaintext, _build_aad(_NEW_AAD_NS, tool_id))
    return {
        "__sealed__": True,
        "aad_ns": _NEW_AAD_NS,
        **_record_to_dict(record),
    }


def unseal_tool_auth(tool_id: uuid.UUID, sealed: dict[str, Any]) -> dict[str, Any]:
    """Decrypt auth from either the old or new AAD namespace.

    Blobs sealed before the rename have no ``aad_ns`` key — they used
    ``mcp_binding_auth:<id>``.  New blobs carry ``aad_ns: "agent_tool_auth"``.
    """
    ns = sealed.get("aad_ns", _LEGACY_AAD_NS)
    record = _dict_to_record(sealed)
    plaintext = env.decrypt_envelope(record, _build_aad(ns, tool_id))
    return json.loads(plaintext.decode("utf-8"))  # type: ignore[no-any-return]


__all__ = ["seal_tool_auth", "unseal_tool_auth"]
