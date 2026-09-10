"""Regression tests: proxy_headers encryption (AC-3)."""

from __future__ import annotations

import base64
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.config.settings import VaultSection
from shared_kernel.infra.vault import VaultClient
from shared_kernel.security.envelope import (
    decrypt_proxy_headers,
    encrypt_proxy_headers,
    envelope_to_jsonb,
    jsonb_to_envelope,
)


class _FakeKv:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def read_secret_version(
        self, *, mount_point: str, path: str, raise_on_deleted_version: bool = True
    ) -> dict[str, Any]:
        return {"data": {"data": self._store[path]}}

    def create_or_update_secret(self, *, mount_point: str, path: str, secret: dict[str, Any]) -> None:
        self._store[path] = secret


class _FakeTransit:
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


@pytest.fixture
def _vault(monkeypatch: pytest.MonkeyPatch) -> VaultClient:
    kv = _FakeKv({"smap/config/hmac-key": {"key": base64.b64encode(b"\x11" * 32).decode()}})
    transit = _FakeTransit()

    class _FakeClient:
        def __init__(self, url: str, **_kw: Any) -> None:
            self.token = None
            self.secrets = SimpleNamespace(transit=transit, kv=SimpleNamespace(v2=kv))

        def is_authenticated(self) -> bool:
            return self.token is not None

    monkeypatch.setattr("shared_kernel.infra.vault.hvac.Client", _FakeClient)
    vc = VaultClient(VaultSection(dev_token="root"))
    monkeypatch.setattr("shared_kernel.auth.clients._vault_instance", vc)
    return vc


def test_encrypt_decrypt_round_trip(_vault: VaultClient) -> None:
    headers = {"X-Proxy-Auth": "Bearer tok123", "X-Region": "us-east-1"}
    key_id = uuid.uuid4()
    encrypted = encrypt_proxy_headers(headers, key_id)
    assert "ct" in encrypted
    assert "nonce" in encrypted
    assert "dek" in encrypted
    assert "hmac" in encrypted

    decrypted = decrypt_proxy_headers(encrypted, key_id)
    assert decrypted == headers


def test_encrypted_not_readable_as_plaintext(_vault: VaultClient) -> None:
    headers = {"X-Secret": "super-secret-token"}
    key_id = uuid.uuid4()
    encrypted = encrypt_proxy_headers(headers, key_id)
    assert "super-secret-token" not in str(encrypted)


def test_proxy_headers_not_in_config_after_pop() -> None:
    from contexts.keys.application.openai_compat_config import OpenAICompatConfig

    cfg = OpenAICompatConfig(
        base_url="https://proxy.example.com",
        proxy_headers={"X-Custom": "val"},
    )
    config_dict = cfg.to_dict()
    proxy_headers = config_dict.pop("proxy_headers", None)
    assert proxy_headers == {"X-Custom": "val"}
    assert "proxy_headers" not in config_dict


def test_legacy_plaintext_fallback() -> None:
    legacy = {"X-Old": "value"}
    key_id = uuid.uuid4()
    result = decrypt_proxy_headers(legacy, key_id)
    assert result == {"X-Old": "value"}


def test_jsonb_serialization_round_trip() -> None:
    from shared_kernel.infra.vault import EnvelopeRecord

    record = EnvelopeRecord(
        ciphertext=b"\x01\x02\x03",
        nonce=b"\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f",
        dek_wrapped="vault:v1:abc123",
        ciphertext_hmac=b"\x10\x11\x12",
        transit_key_version=1,
        hmac_key_version=1,
    )
    jsonb = envelope_to_jsonb(record)
    restored = jsonb_to_envelope(jsonb)
    assert restored.ciphertext == record.ciphertext
    assert restored.nonce == record.nonce
    assert restored.dek_wrapped == record.dek_wrapped
    assert restored.ciphertext_hmac == record.ciphertext_hmac
    assert restored.transit_key_version == record.transit_key_version
    assert restored.hmac_key_version == record.hmac_key_version
