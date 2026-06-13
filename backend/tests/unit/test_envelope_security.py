"""D.1 — envelope security wrapper: AAD builders, version capture, rewrap."""

from __future__ import annotations

import base64
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.config.settings import VaultSection
from shared_kernel.infra.vault import VaultClient, VaultError
from shared_kernel.security import envelope as env


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


class _FakeTransit:
    """Pretend Transit. Current Transit version advances on rotate()."""

    MASTER = b"\x42" * 32

    def __init__(self) -> None:
        self.version = 1

    def generate_data_key(self, *, name: str, key_type: str, bits: int) -> dict[str, Any]:
        dek = os.urandom(bits // 8)
        wrapped = bytes(d ^ m for d, m in zip(dek, self.MASTER * 100, strict=False))
        ciphertext = f"vault:v{self.version}:" + base64.b64encode(wrapped).decode()
        return {"data": {"plaintext": base64.b64encode(dek).decode(), "ciphertext": ciphertext}}

    def decrypt_data(self, *, name: str, ciphertext: str) -> dict[str, Any]:
        wrapped = base64.b64decode(ciphertext.split(":", 2)[2])
        dek = bytes(w ^ m for w, m in zip(wrapped, self.MASTER * 100, strict=False))
        return {"data": {"plaintext": base64.b64encode(dek).decode()}}

    def rewrap_data(self, *, name: str, ciphertext: str) -> dict[str, Any]:
        # Same wrapped payload, new version prefix — mirrors real Vault.
        payload = ciphertext.split(":", 2)[2]
        return {"data": {"ciphertext": f"vault:v{self.version}:{payload}"}}

    def rotate(self) -> None:
        self.version += 1


@pytest.fixture
def vault_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[VaultClient, _FakeTransit]:
    kv = _FakeKv(
        {
            "smap/config/hmac-key": {
                "key": base64.b64encode(b"\x11" * 32).decode(),
                "version": 1,
            }
        }
    )
    transit = _FakeTransit()

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
    client = VaultClient(VaultSection(dev_token="root"))
    monkeypatch.setattr("shared_kernel.auth.clients.get_vault_client", lambda settings=None: client)
    return client, transit


def test_api_key_aad_namespace_stability() -> None:
    # Changing the prefix silently breaks every existing at-rest row.
    kid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert env.api_key_aad(kid) == b"api_keys:00000000-0000-0000-0000-000000000001"


def test_search_key_aad_distinct_from_api_key_aad() -> None:
    kid = uuid.uuid4()
    assert env.api_key_aad(kid) != env.search_key_aad(kid)


def test_seal_unseal_roundtrip(vault_fixture) -> None:
    _client, _transit = vault_fixture
    kid = uuid.uuid4()
    aad = env.api_key_aad(kid)
    record = env.encrypt_envelope(b"sk-ant-abcdef", aad)
    assert env.decrypt_envelope(record, aad) == b"sk-ant-abcdef"


def test_seal_captures_transit_and_hmac_versions(vault_fixture) -> None:
    _client, _transit = vault_fixture
    record = env.encrypt_envelope(b"x", env.api_key_aad(uuid.uuid4()))
    assert record.transit_key_version == 1
    assert record.hmac_key_version == 1


def test_aad_mismatch_rejects(vault_fixture) -> None:
    _client, _transit = vault_fixture
    a = uuid.uuid4()
    b = uuid.uuid4()
    record = env.encrypt_envelope(b"secret", env.api_key_aad(a))
    with pytest.raises(VaultError, match="HMAC mismatch"):
        env.decrypt_envelope(record, env.api_key_aad(b))


def test_cross_namespace_aad_rejects(vault_fixture) -> None:
    _client, _transit = vault_fixture
    kid = uuid.uuid4()
    record = env.encrypt_envelope(b"secret", env.api_key_aad(kid))
    # Same UUID, different table namespace — must not decrypt.
    with pytest.raises(VaultError, match="HMAC mismatch"):
        env.decrypt_envelope(record, env.search_key_aad(kid))


def test_rewrap_bumps_transit_version_and_keeps_plaintext(vault_fixture) -> None:
    _client, transit = vault_fixture
    aad = env.api_key_aad(uuid.uuid4())
    record = env.encrypt_envelope(b"secret-payload", aad)
    assert record.transit_key_version == 1

    transit.rotate()
    rewrapped = env.rewrap_envelope(record)
    assert rewrapped.transit_key_version == 2
    assert rewrapped.ciphertext == record.ciphertext
    assert rewrapped.ciphertext_hmac == record.ciphertext_hmac
    assert env.decrypt_envelope(rewrapped, aad) == b"secret-payload"


def test_unknown_hmac_version_shape_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    kv = _FakeKv(
        {
            "smap/config/hmac-key": {
                "key": base64.b64encode(b"\x11" * 32).decode(),
                "version": "not-an-int",
            }
        }
    )

    class _FakeClient:
        def __init__(self, url: str) -> None:
            self.token = None
            self.secrets = SimpleNamespace(
                transit=_FakeTransit(),
                kv=SimpleNamespace(v2=kv),
            )

        def is_authenticated(self) -> bool:
            return self.token is not None

    monkeypatch.setattr("shared_kernel.infra.vault.hvac.Client", _FakeClient)
    client = VaultClient(VaultSection(dev_token="root"))
    with pytest.raises(VaultError, match="non-integer"):
        client.encrypt_envelope(b"x", b"aad")
