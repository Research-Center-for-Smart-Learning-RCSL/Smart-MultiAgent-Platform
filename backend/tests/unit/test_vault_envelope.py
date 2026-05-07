"""B.2 — envelope encrypt/decrypt round-trip with mocked Vault datakey + KV."""

from __future__ import annotations

import base64
import os
from types import SimpleNamespace
from typing import Any

import pytest

from app.config.settings import VaultSection
from shared_kernel.infra.vault import VaultClient, VaultError


class _FakeKv:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def read_secret_version(
        self, *, mount_point: str, path: str, raise_on_deleted_version: bool = True
    ) -> dict[str, Any]:
        if path not in self._store:
            import hvac.exceptions

            raise hvac.exceptions.InvalidPath(f"{mount_point}/{path}")
        return {"data": {"data": self._store[path]}}

    def create_or_update_secret(self, *, mount_point: str, path: str, secret: dict[str, Any]) -> None:
        self._store[path] = secret


class _FakeTransitEnvelope:
    """Pretend Transit: DEK wrap = xor-over-length, decrypt = same xor."""

    MASTER = b"\x42" * 32

    def generate_data_key(self, *, name: str, key_type: str, bits: int) -> dict[str, Any]:
        dek = os.urandom(bits // 8)
        wrapped = bytes(d ^ m for d, m in zip(dek, self.MASTER * 100, strict=False))
        ciphertext = "vault:v1:" + base64.b64encode(wrapped).decode()
        return {"data": {"plaintext": base64.b64encode(dek).decode(), "ciphertext": ciphertext}}

    def decrypt_data(self, *, name: str, ciphertext: str) -> dict[str, Any]:
        wrapped = base64.b64decode(ciphertext.split(":", 2)[2])
        dek = bytes(w ^ m for w, m in zip(wrapped, self.MASTER * 100, strict=False))
        return {"data": {"plaintext": base64.b64encode(dek).decode()}}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> VaultClient:
    kv = _FakeKv(
        {
            "smap/config/hmac-key": {
                "key": base64.b64encode(b"\x11" * 32).decode(),
            }
        }
    )
    transit = _FakeTransitEnvelope()

    class _FakeClient:
        def __init__(self, url: str) -> None:
            self.token = None
            self.secrets = SimpleNamespace(
                transit=transit,
                kv=SimpleNamespace(v2=kv),
            )

        def is_authenticated(self) -> bool:
            return self.token is not None

    monkeypatch.setattr("shared_kernel.infra.vault.hvac.Client", _FakeClient)
    return VaultClient(VaultSection(dev_token="root"))


def test_envelope_roundtrip(client: VaultClient) -> None:
    secret = b"sk-live-" + os.urandom(48)
    aad = b"user=123|provider=openai"
    record = client.encrypt_envelope(secret, aad)
    assert record.ciphertext != secret
    assert record.nonce
    assert len(record.nonce) == 12
    assert record.dek_wrapped.startswith("vault:v1:")
    assert client.decrypt_envelope(record, aad) == secret


def test_envelope_hmac_detects_tamper(client: VaultClient) -> None:
    record = client.encrypt_envelope(b"cleartext", b"aad")
    tampered = type(record)(
        ciphertext=record.ciphertext[:-1] + bytes([record.ciphertext[-1] ^ 0x01]),
        nonce=record.nonce,
        dek_wrapped=record.dek_wrapped,
        ciphertext_hmac=record.ciphertext_hmac,
    )
    with pytest.raises(VaultError, match="HMAC mismatch"):
        client.decrypt_envelope(tampered, b"aad")
