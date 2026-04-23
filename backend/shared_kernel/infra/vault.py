"""Typed Vault client (B.2).

Responsibilities (the ONLY things this module is allowed to do):

  * Provider-secret envelope encryption (§7.6 R7.06) — `encrypt_envelope`,
    `decrypt_envelope`, `create_dek`, `unwrap_dek` against the Transit key
    `smap-provider-secret`.
  * Guest-link ed25519 sign/verify against `smap-guest-link`.
  * JWT RS256 sign/verify against `smap-jwt-sign`, with in-process public-key
    cache keyed by `kid` = Vault key version (R6.03).
  * KV v2 read/write on `secret/smap/config/*`.
  * AppRole login + silent re-login on 403.

Out of scope:
  * HTTP serving (those are `app.api.*`).
  * Business logic (those are `contexts.*`).
  * Bootstrap-only one-shots (those are `smap.bootstrap.*`) — note that the
    bootstrap CLI still imports a different entry point on this module that
    takes a raw root/dev token so it does not need AppRoles to exist yet.

SoC: nothing in this file imports from `contexts.*` or `app.*`.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Final

import hvac
import orjson
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidSignature

from app.config.settings import VaultSection

_KV_HMAC_PATH: Final = "smap/config/hmac-key"
_HMAC_KEY_BYTES: Final = 32
_DEK_BITS: Final = 256
_GCM_NONCE_BYTES: Final = 12


class VaultError(RuntimeError):
    """Raised when Vault is configured correctly but returned an error."""


# ---------------------------------------------------------------------------
# Envelope record — the Postgres shape documented in §7.6.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnvelopeRecord:
    """Matches `api_keys` / `search_keys` columns.

    Version fields are captured at write time so D.10 (`smap-rotation rotate-transit`)
    can (a) locate rows that still reference a retired transit version and
    (b) decide whether a stored HMAC is still in the accepted grace window.
    They are metadata only — `decrypt_envelope` does not consult them because
    Vault self-describes the transit version in the wrap prefix and the HMAC
    key is addressed by the row's version directly.
    """

    ciphertext: bytes
    nonce: bytes
    dek_wrapped: str                # `vault:v{N}:{b64}` — opaque to the caller
    ciphertext_hmac: bytes          # HMAC-SHA256(ciphertext || nonce || aad) with KV key
    transit_key_version: int = 0    # parsed from `dek_wrapped`; 0 ⇒ unparsed (legacy)
    hmac_key_version: int = 1       # KV-tracked; defaults to 1 before D.10 rotation runs


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class VaultClient:
    """Singleton wrapper around hvac with AppRole auth + local crypto helpers."""

    def __init__(self, cfg: VaultSection) -> None:
        self._cfg = cfg
        self._client = hvac.Client(url=cfg.addr)
        self._lock = threading.Lock()
        self._pubkey_cache: dict[int, RSAPublicKey] = {}
        self._pubkey_fetched_at: float = 0.0
        self._key_config: dict[str, Any] = {}
        self._hmac_key: bytes | None = None
        self._hmac_version: int = 1
        self._last_login: float = 0.0
        self._login()

    # -------------------- auth --------------------

    def _login(self) -> None:
        """AppRole if configured, else dev token (local compose only)."""
        if self._cfg.role_id and self._cfg.secret_id:
            resp = self._client.auth.approle.login(
                role_id=self._cfg.role_id,
                secret_id=self._cfg.secret_id,
            )
            token = resp["auth"]["client_token"]
            self._client.token = token
        elif self._cfg.dev_token:
            self._client.token = self._cfg.dev_token
        else:
            raise VaultError(
                "Vault is not configured: set SMAP_VAULT_ROLE_ID + "
                "SMAP_VAULT_SECRET_ID for prod, or SMAP_VAULT_DEV_TOKEN for dev."
            )
        if not self._client.is_authenticated():
            raise VaultError("Vault authentication failed.")
        self._last_login = time.monotonic()

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke hvac; on 403 re-login once and retry (token rotation safety)."""
        try:
            return fn(*args, **kwargs)
        except hvac.exceptions.Forbidden:
            with self._lock:
                # Only relogin if we haven't just done so (thundering-herd guard).
                if time.monotonic() - self._last_login > 1.0:
                    self._login()
            return fn(*args, **kwargs)

    # -------------------- envelope --------------------

    def create_dek(self) -> tuple[bytes, str]:
        """Request a fresh wrapped DEK from Vault.

        Returns (plaintext_dek, wrapped_ciphertext_str). Caller MUST zeroize the
        plaintext after use — we cannot enforce that here because Python strings
        live in the GC heap; the contract is documented in §7.6.
        """
        resp = self._call(
            self._client.secrets.transit.generate_data_key,
            name=self._cfg.transit_key_provider,
            key_type="plaintext",
            bits=_DEK_BITS,
        )
        data = resp["data"]
        plaintext = base64.b64decode(data["plaintext"])
        return plaintext, data["ciphertext"]

    def unwrap_dek(self, wrapped: str) -> bytes:
        resp = self._call(
            self._client.secrets.transit.decrypt_data,
            name=self._cfg.transit_key_provider,
            ciphertext=wrapped,
        )
        return base64.b64decode(resp["data"]["plaintext"])

    def encrypt_envelope(self, plaintext: bytes, aad: bytes) -> EnvelopeRecord:
        dek, wrapped = self.create_dek()
        try:
            nonce = os.urandom(_GCM_NONCE_BYTES)
            ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        finally:
            # Best-effort zeroization — Python does not guarantee it.
            dek = b"\x00" * len(dek)  # noqa: F841
        mac = hmac.new(
            self._load_hmac_key(),
            ciphertext + nonce + aad,
            sha256,
        ).digest()
        return EnvelopeRecord(
            ciphertext=ciphertext,
            nonce=nonce,
            dek_wrapped=wrapped,
            ciphertext_hmac=mac,
            transit_key_version=_parse_transit_version(wrapped),
            hmac_key_version=self._hmac_version,
        )

    def decrypt_envelope(self, record: EnvelopeRecord, aad: bytes) -> bytes:
        expected = hmac.new(
            self._load_hmac_key(),
            record.ciphertext + record.nonce + aad,
            sha256,
        ).digest()
        if not hmac.compare_digest(expected, record.ciphertext_hmac):
            try:
                from shared_kernel.observability.metrics import ENVELOPE_DECRYPT_FAILURES_TOTAL
                ENVELOPE_DECRYPT_FAILURES_TOTAL.inc()
            except Exception:  # noqa: BLE001
                pass
            raise VaultError("Envelope HMAC mismatch — tampered or wrong key.")
        dek = self.unwrap_dek(record.dek_wrapped)
        try:
            return AESGCM(dek).decrypt(record.nonce, record.ciphertext, aad)
        finally:
            dek = b"\x00" * len(dek)  # noqa: F841

    def _load_hmac_key(self) -> bytes:
        if self._hmac_key is not None:
            return self._hmac_key
        raw = self.kv_get(_KV_HMAC_PATH)
        encoded = raw.get("key")
        if not encoded:
            raise VaultError(
                f"KV {_KV_HMAC_PATH!r} missing key `key` — run `smap.bootstrap vault-init`."
            )
        key = base64.b64decode(encoded)
        if len(key) != _HMAC_KEY_BYTES:
            raise VaultError(f"HMAC key must be {_HMAC_KEY_BYTES} bytes; got {len(key)}.")
        # `version` is optional on first install; D.10 rotation writes a
        # monotonically incrementing int alongside the seed bytes.
        try:
            version = int(raw.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise VaultError(f"KV {_KV_HMAC_PATH!r} has non-integer `version`.") from exc
        if version < 1:
            raise VaultError(f"HMAC key version must be >= 1; got {version}.")
        self._hmac_key = key
        self._hmac_version = version
        return key

    def rewrap_dek(self, wrapped: str) -> str:
        """Re-wrap an existing DEK against the current Transit version.

        Used by `smap.rotation rotate-transit` (D.10). Vault never returns the
        plaintext DEK during this operation — the unwrap/wrap cycle happens
        entirely inside Vault. The caller persists the returned string and the
        new parsed `transit_key_version`.
        """
        resp = self._call(
            self._client.secrets.transit.rewrap_data,
            name=self._cfg.transit_key_provider,
            ciphertext=wrapped,
        )
        return str(resp["data"]["ciphertext"])

    # -------------------- guest-link (ed25519) --------------------

    def sign_guest_link(self, payload: bytes) -> str:
        resp = self._call(
            self._client.secrets.transit.sign_data,
            name=self._cfg.transit_key_guest,
            hash_input=base64.b64encode(payload).decode(),
        )
        return str(resp["data"]["signature"])  # `vault:v{N}:{b64}`

    def verify_guest_link(self, payload: bytes, signature: str) -> bool:
        resp = self._call(
            self._client.secrets.transit.verify_signed_data,
            name=self._cfg.transit_key_guest,
            hash_input=base64.b64encode(payload).decode(),
            signature=signature,
        )
        return bool(resp["data"]["valid"])

    # -------------------- JWT (RS256 via Transit) --------------------

    def sign_jwt(self, claims: dict[str, Any]) -> str:
        """Sign `claims` as a compact JWS using the current `smap-jwt-sign` version."""
        version = self._latest_jwt_version()
        header = {"alg": "RS256", "typ": "JWT", "kid": str(version)}
        signing_input = _b64url(orjson.dumps(header)) + b"." + _b64url(orjson.dumps(claims))
        resp = self._call(
            self._client.secrets.transit.sign_data,
            name=self._cfg.transit_key_jwt,
            hash_input=base64.b64encode(signing_input).decode(),
            hash_algorithm="sha2-256",
            signature_algorithm="pkcs1v15",
            key_version=version,
            prehashed=False,
        )
        vault_sig: str = resp["data"]["signature"]
        raw_sig = base64.b64decode(vault_sig.split(":", 2)[2])
        return (signing_input + b"." + _b64url(raw_sig)).decode("ascii")

    def verify_jwt(self, token: str) -> dict[str, Any]:
        """Return claims if the signature verifies and `kid` is inside the
        accepted overlap window; else raise VaultError.
        """
        try:
            header_b64, claims_b64, sig_b64 = token.split(".")
        except ValueError as exc:
            raise VaultError("Malformed JWT.") from exc

        header = orjson.loads(_b64url_decode(header_b64))
        if header.get("alg") != "RS256":
            raise VaultError("Only RS256 is accepted.")
        try:
            kid = int(header["kid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultError("JWT kid missing or non-integer.") from exc

        pub = self._public_key_for(kid)
        signing_input = (header_b64 + "." + claims_b64).encode("ascii")
        try:
            pub.verify(
                _b64url_decode(sig_b64),
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise VaultError("JWT signature invalid.") from exc

        return orjson.loads(_b64url_decode(claims_b64))  # type: ignore[no-any-return]

    def _latest_jwt_version(self) -> int:
        self._ensure_jwt_cache(force=False)
        return int(self._key_config.get("latest_version", 1))

    def _public_key_for(self, kid: int) -> RSAPublicKey:
        # Always attempt a soft refresh so min_decryption_version changes are
        # picked up when the 60 s cache window expires.  Force a hard refresh
        # only when the kid itself is unknown (e.g. newly-rotated key).
        force = kid not in self._pubkey_cache
        self._ensure_jwt_cache(force=force)
        if kid not in self._pubkey_cache:
            raise VaultError(f"Unknown JWT kid={kid} (rotated out or never issued).")
        min_ok = int(self._key_config.get("min_decryption_version", 1))
        if kid < min_ok:
            raise VaultError(f"JWT kid={kid} below min_decryption_version={min_ok}.")
        return self._pubkey_cache[kid]

    def _ensure_jwt_cache(self, *, force: bool) -> None:
        # Cache for 60 s to amortise rotation polls without hiding them forever.
        if not force and time.monotonic() - self._pubkey_fetched_at < 60.0:
            return
        resp = self._call(
            self._client.secrets.transit.read_key, name=self._cfg.transit_key_jwt
        )
        data = resp["data"]
        self._key_config = data
        versions = data.get("keys", {})
        new_cache: dict[int, RSAPublicKey] = {}
        for k, meta in versions.items():
            try:
                version = int(k)
            except ValueError:
                continue
            pem = meta.get("public_key") if isinstance(meta, dict) else None
            if not pem:
                continue
            loaded = serialization.load_pem_public_key(pem.encode())
            if not isinstance(loaded, RSAPublicKey):
                raise VaultError("smap-jwt-sign returned a non-RSA public key.")
            new_cache[version] = loaded
        self._pubkey_cache = new_cache
        self._pubkey_fetched_at = time.monotonic()

    # -------------------- KV v2 --------------------
    #
    # `path` is the full logical path under the KV mount, e.g.
    # `"smap/config/hmac-key"`. The mount (default `"secret"`) is taken from
    # settings; callers never spell it.

    def kv_get(self, path: str) -> dict[str, Any]:
        resp = self._call(
            self._client.secrets.kv.v2.read_secret_version,
            mount_point=self._cfg.kv_mount,
            path=path,
            raise_on_deleted_version=True,
        )
        return dict(resp["data"]["data"])

    def kv_put(self, path: str, data: dict[str, Any]) -> None:
        self._call(
            self._client.secrets.kv.v2.create_or_update_secret,
            mount_point=self._cfg.kv_mount,
            path=path,
            secret=data,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(data: str | bytes) -> bytes:
    if isinstance(data, str):
        data = data.encode("ascii")
    pad = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + b"=" * pad)


def new_hmac_seed() -> bytes:
    """Exposed so the bootstrap CLI can seed `secret/smap/config/hmac-key`."""
    return secrets.token_bytes(_HMAC_KEY_BYTES)


def _parse_transit_version(wrapped: str) -> int:
    """Extract the `N` from Vault's `vault:vN:{b64}` ciphertext prefix.

    Returning 0 for unrecognised shapes keeps legacy rows loadable; D.10
    treats 0 as "needs rewrap" when walking the table.
    """
    try:
        prefix = wrapped.split(":", 2)
        if len(prefix) >= 2 and prefix[0] == "vault" and prefix[1].startswith("v"):
            return int(prefix[1][1:])
    except (ValueError, IndexError):
        pass
    return 0


__all__ = [
    "EnvelopeRecord",
    "VaultClient",
    "VaultError",
    "new_hmac_seed",
]
