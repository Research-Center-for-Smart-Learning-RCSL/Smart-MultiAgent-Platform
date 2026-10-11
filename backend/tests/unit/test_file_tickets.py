"""Signed download tickets for `/api/files/{ticket}` ([R19a.14]).

Dossier: docs/tasks/2026-10-11-browser-file-delivery/spec.md §7.1, §8.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from shared_kernel.auth import clients
from shared_kernel.storage import file_tickets
from shared_kernel.storage.file_tickets import TicketExpired, TicketInvalid

_T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class _Signer:
    """Stands in for the Vault HMAC seed: versioned keys, current = highest."""

    def __init__(self) -> None:
        self.keys = {1: b"k" * 32}

    @property
    def current(self) -> int:
        return max(self.keys)

    def hmac_sign(self, data: bytes) -> tuple[int, bytes]:
        v = self.current
        return v, hmac.new(self.keys[v], data, hashlib.sha256).digest()

    def hmac_verify(self, version: int, data: bytes, mac: bytes) -> bool:
        key = self.keys.get(version)
        if key is None:
            return False
        return hmac.compare_digest(hmac.new(key, data, hashlib.sha256).digest(), mac)


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Signer]:
    s = _Signer()
    monkeypatch.setattr(file_tickets, "_signer", lambda: s)
    clock = {"now": _T0}
    clients.set_clock(lambda: clock["now"])
    yield s
    clients.set_clock(lambda: datetime.now(UTC))


def _ticket_of(path: str) -> str:
    assert path.startswith("/api/files/")
    return path.removeprefix("/api/files/")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


async def _issue(**kw: object) -> str:
    args: dict[str, object] = {
        "bucket": "exports",
        "key": "job/export.md",
        "content_type": "text/markdown",
        "disposition": 'attachment; filename="room.md"',
    }
    args.update(kw)
    return _ticket_of(await file_tickets.issue(**args))  # type: ignore[arg-type]


async def test_round_trip(signer: _Signer) -> None:
    t = await _issue()
    got = await file_tickets.verify(t)
    assert got.bucket == "exports"
    assert got.key == "job/export.md"
    assert got.content_type == "text/markdown"
    assert got.disposition == 'attachment; filename="room.md"'
    assert got.expires_at == _T0 + timedelta(minutes=15)


async def test_path_is_url_safe(signer: _Signer) -> None:
    t = await _issue(
        key="proj/room/att/名稱 with space.png", disposition="inline; filename*=UTF-8''%E5%90%8D"
    )
    assert all(c.isalnum() or c in "-_." for c in t)
    assert (await file_tickets.verify(t)).key == "proj/room/att/名稱 with space.png"


async def test_tampered_payload_is_invalid(signer: _Signer) -> None:
    payload_b64, mac_b64 = (await _issue()).split(".")
    payload = json.loads(_unb64(payload_b64))
    payload["k"] = "other-job/export.md"
    forged = _b64(json.dumps(payload).encode()) + "." + mac_b64
    with pytest.raises(TicketInvalid):
        await file_tickets.verify(forged)


async def test_tampered_mac_is_invalid(signer: _Signer) -> None:
    payload_b64, mac_b64 = (await _issue()).split(".")
    mac = bytearray(_unb64(mac_b64))
    mac[-1] ^= 0x01
    with pytest.raises(TicketInvalid):
        await file_tickets.verify(payload_b64 + "." + _b64(bytes(mac)))


async def test_mac_without_domain_prefix_is_invalid(signer: _Signer) -> None:
    """A MAC over the bare payload (as an envelope HMAC would be) must not verify."""
    payload_b64, _ = (await _issue()).split(".")
    version, bare = signer.hmac_sign(_unb64(payload_b64))
    sig = version.to_bytes(2, "big") + bare
    with pytest.raises(TicketInvalid):
        await file_tickets.verify(payload_b64 + "." + _b64(sig))


async def test_expired(signer: _Signer) -> None:
    t = await _issue()
    clients.set_clock(lambda: _T0 + timedelta(minutes=15, seconds=1))
    with pytest.raises(TicketExpired):
        await file_tickets.verify(t)


async def test_foreign_bucket_is_invalid_even_when_signed(signer: _Signer) -> None:
    payload_b64, _ = (await _issue()).split(".")
    payload = json.loads(_unb64(payload_b64))
    payload["b"] = "rag-sources"
    raw = json.dumps(payload).encode()
    version, mac = signer.hmac_sign(b"smap-file-ticket:v1\x00" + raw)
    forged = _b64(raw) + "." + _b64(version.to_bytes(2, "big") + mac)
    with pytest.raises(TicketInvalid, match="bucket"):
        await file_tickets.verify(forged)


async def test_issue_refuses_foreign_bucket(signer: _Signer) -> None:
    with pytest.raises(ValueError, match="not browser-deliverable"):
        await _issue(bucket="agent-workspace")


async def test_unknown_key_version_is_invalid(signer: _Signer) -> None:
    t = await _issue()
    signer.keys = {2: b"z" * 32}
    with pytest.raises(TicketInvalid):
        await file_tickets.verify(t)


async def test_old_key_version_still_verifies_after_rotation(signer: _Signer) -> None:
    t = await _issue()
    signer.keys[2] = b"z" * 32
    assert (await file_tickets.verify(t)).key == "job/export.md"


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "!!!.???", "e30.AAAA"])
async def test_malformed_is_invalid(signer: _Signer, garbage: str) -> None:
    with pytest.raises(TicketInvalid):
        await file_tickets.verify(garbage)


async def test_rate_key_is_stable_and_not_the_ticket(signer: _Signer) -> None:
    t = await _issue()
    a = await file_tickets.verify(t)
    b = await file_tickets.verify(t)
    assert a.rate_key == b.rate_key
    assert a.rate_key not in t
