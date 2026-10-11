"""`VaultClient.hmac_sign` / `hmac_verify` — the seed stays inside the client."""

from __future__ import annotations

import time

import pytest

from shared_kernel.infra.vault import VaultClient, VaultError


def _client(keys: dict[int, bytes], current: int) -> VaultClient:
    c = object.__new__(VaultClient)
    c._hmac_keys = dict(keys)
    c._hmac_current_version = current
    c._hmac_current_at = time.monotonic()
    c._hmac_missing = {}
    return c


def test_sign_uses_current_version_and_verifies() -> None:
    c = _client({1: b"a" * 32, 2: b"b" * 32}, current=2)
    version, mac = c.hmac_sign(b"data")
    assert version == 2
    assert c.hmac_verify(2, b"data", mac)
    assert not c.hmac_verify(1, b"data", mac)
    assert not c.hmac_verify(2, b"other", mac)


def test_verify_is_false_when_version_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client({1: b"a" * 32}, current=1)

    def _missing(version: int) -> bytes:
        raise VaultError("trimmed history")

    monkeypatch.setattr(c, "_hmac_for_version", _missing)
    assert not c.hmac_verify(7, b"data", b"\x00" * 32)


def test_version_above_current_is_refused_without_a_kv_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forged ticket picks its own seed version; it must not cost a Vault read."""
    c = _client({1: b"a" * 32}, current=1)
    reads: list[int] = []
    def _read(path: str, version: int) -> dict[str, object]:
        reads.append(version)
        return {}

    monkeypatch.setattr(c, "kv_get_version", _read)
    assert not c.hmac_verify(65000, b"data", b"\x00" * 32)
    assert reads == []


def test_unreadable_old_version_is_negatively_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client({3: b"c" * 32}, current=3)
    calls: list[int] = []

    def _missing(version: int) -> bytes:
        calls.append(version)
        raise VaultError("trimmed history")

    monkeypatch.setattr(c, "_hmac_for_version", _missing)
    assert not c.hmac_verify(2, b"data", b"\x00" * 32)
    assert not c.hmac_verify(2, b"data", b"\x00" * 32)
    assert calls == [2]


def test_outage_is_not_reported_as_a_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client({}, current=1)
    c._hmac_current_at = 0.0

    def _down(path: str) -> dict[str, object]:
        raise ConnectionError("vault unreachable")

    monkeypatch.setattr(c, "kv_get", _down)
    with pytest.raises(ConnectionError):
        c.hmac_verify(1, b"data", b"\x00" * 32)
