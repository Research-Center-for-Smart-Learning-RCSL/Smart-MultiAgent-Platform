"""B.2 — JWT sign/verify round-trip survives a forced kid rotation.

We stub `hvac.Client` at the network boundary and drive the class through a
simulated single-version → two-version state transition. The real RSA math
is done by `cryptography` so the test is meaningful without a live Vault.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config.settings import VaultSection
from shared_kernel.infra.vault import VaultClient


def _pem_pair() -> tuple[rsa.RSAPrivateKey, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, pem


class _FakeTransit:
    def __init__(self) -> None:
        self.versions: dict[int, rsa.RSAPrivateKey] = {}
        self.pem: dict[int, bytes] = {}
        self.min_decryption_version = 1

    def add_version(self, n: int) -> None:
        key, pem = _pem_pair()
        self.versions[n] = key
        self.pem[n] = pem

    # hvac surface ----------------------------------------------------------

    def read_key(self, *, name: str) -> dict[str, Any]:
        keys = {
            str(n): {"public_key": self.pem[n].decode()}
            for n in self.versions
        }
        return {
            "data": {
                "type": "rsa-2048",
                "keys": keys,
                "latest_version": max(self.versions),
                "min_decryption_version": self.min_decryption_version,
            }
        }

    def sign_data(
        self,
        *,
        name: str,
        hash_input: str,
        hash_algorithm: str = "sha2-256",
        signature_algorithm: str = "pkcs1v15",
        key_version: int | None = None,
        prehashed: bool = False,
    ) -> dict[str, Any]:
        data = base64.b64decode(hash_input)
        ver = key_version or max(self.versions)
        sig = self.versions[ver].sign(data, padding.PKCS1v15(), hashes.SHA256())
        return {
            "data": {"signature": f"vault:v{ver}:{base64.b64encode(sig).decode()}"}
        }


@pytest.fixture
def vault_client(monkeypatch: pytest.MonkeyPatch) -> tuple[VaultClient, _FakeTransit]:
    transit = _FakeTransit()
    transit.add_version(1)

    class _FakeClient:
        def __init__(self, url: str) -> None:
            self.token = None
            self.secrets = SimpleNamespace(transit=transit)

        def is_authenticated(self) -> bool:
            return self.token is not None

    monkeypatch.setattr("shared_kernel.infra.vault.hvac.Client", _FakeClient)

    cfg = VaultSection(dev_token="root")
    client = VaultClient(cfg)
    return client, transit


def test_sign_and_verify_roundtrip(vault_client):  # noqa: ANN001
    client, _transit = vault_client
    claims = {"sub": "u-1", "iss": "smap.local"}
    token = client.sign_jwt(claims)
    assert client.verify_jwt(token) == claims


def test_verify_survives_kid_rotation(vault_client):  # noqa: ANN001
    client, transit = vault_client
    token_old = client.sign_jwt({"sub": "u-old"})

    # Rotate: add version 2, bust the pubkey cache to force refresh.
    transit.add_version(2)
    client._pubkey_fetched_at = 0.0
    client._key_config = {}

    # Old token signed under v1 MUST still verify while v1 ≥ min_decryption_version.
    assert client.verify_jwt(token_old)["sub"] == "u-old"

    # New signatures use v2.
    token_new = client.sign_jwt({"sub": "u-new"})
    assert token_new.split(".")[0]  # well-formed
    assert client.verify_jwt(token_new)["sub"] == "u-new"

    # Advance min_decryption_version past v1 → old token rejected.
    transit.min_decryption_version = 2
    client._pubkey_fetched_at = 0.0
    client._key_config = {}
    from shared_kernel.infra.vault import VaultError

    with pytest.raises(VaultError, match="below min_decryption_version"):
        client.verify_jwt(token_old)
