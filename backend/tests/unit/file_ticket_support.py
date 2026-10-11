"""Shared test support for code that issues `/api/files/{ticket}` links.

The `ticket_signer` fixture (registered in `tests/unit/conftest.py`) swaps the
Vault seed for `FakeSigner`; `decode(url)` reads back what a link authorises.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from shared_kernel.storage import file_tickets
from shared_kernel.storage.file_tickets import FileTicket


class FakeSigner:
    key = b"t" * 32

    def hmac_sign(self, data: bytes) -> tuple[int, bytes]:
        return 1, hmac.new(self.key, data, hashlib.sha256).digest()

    def hmac_verify(self, version: int, data: bytes, mac: bytes) -> bool:
        return version == 1 and hmac.compare_digest(self.hmac_sign(data)[1], mac)


def install_fake_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_tickets, "_signer", FakeSigner)


def assert_browser_url(url: str | None) -> str:
    """A link handed to a browser points at the backend, never the storage plane."""
    assert url is not None
    assert url.startswith(file_tickets.PATH_PREFIX), url
    assert "minio" not in url
    return url


async def decode(url: str | None) -> FileTicket:
    return await file_tickets.verify(assert_browser_url(url).removeprefix(file_tickets.PATH_PREFIX))
