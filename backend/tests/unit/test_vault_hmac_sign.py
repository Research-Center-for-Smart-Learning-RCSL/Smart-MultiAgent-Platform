"""`VaultClient.hmac_sign` / `hmac_verify` — the seed stays inside the client."""

from __future__ import annotations

import base64
import time

import pytest

from shared_kernel.infra.vault import VaultClient, VaultError


def _client(keys: dict[int, bytes], current: int) -> VaultClient:
    c = object.__new__(VaultClient)
    c._hmac_keys = dict(keys)
    c._hmac_current_version = current
    c._hmac_current_at = time.monotonic()
    c._hmac_missing = {}
    c._hmac_recheck_at = float("-inf")
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


def test_version_above_current_costs_at_most_one_read_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forged ticket picks its own seed version; a flood of them must not
    become one Vault read per request."""
    c = _client({1: b"a" * 32}, current=1)
    reads: list[str] = []

    def _current(path: str) -> dict[str, object]:
        reads.append(path)
        return {"key": base64.b64encode(b"a" * 32).decode(), "version": 1}

    def _history(path: str, version: int) -> dict[str, object]:
        raise AssertionError("an unseen version must never reach a history read")

    monkeypatch.setattr(c, "kv_get", _current)
    monkeypatch.setattr(c, "kv_get_version", _history)
    for _ in range(50):
        assert not c.hmac_verify(65000, b"data", b"\x00" * 32)
    assert reads == ["smap/config/hmac-key"]


def test_ticket_from_a_peer_that_already_rotated_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another worker refreshed first and signs with v2 while this one still
    caches v1 as current; the ticket must not 404 until the TTL runs out."""
    peer = _client({2: b"b" * 32}, current=2)
    version, mac = peer.hmac_sign(b"data")
    c = _client({1: b"a" * 32}, current=1)
    monkeypatch.setattr(c, "kv_get", lambda path: {"key": base64.b64encode(b"b" * 32).decode(), "version": 2})
    assert c.hmac_verify(version, b"data", mac)
    assert c.hmac_sign(b"x")[0] == 2


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


def test_failed_refresh_backs_off_instead_of_hitting_vault_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Vault outage plus a flood of forged tickets must not become one KV read
    per request on the worker thread pool."""
    c = _client({1: b"a" * 32}, current=1)
    c._hmac_current_at = 1.0  # stale: the next call tries to refresh
    reads: list[str] = []

    def _down(path: str) -> dict[str, object]:
        reads.append(path)
        raise ConnectionError("vault unreachable")

    monkeypatch.setattr(c, "kv_get", _down)
    c.hmac_sign(b"a")
    c.hmac_sign(b"b")
    assert reads == ["smap/config/hmac-key"]
