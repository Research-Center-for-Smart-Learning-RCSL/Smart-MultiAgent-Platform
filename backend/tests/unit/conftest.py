"""Unit-tier fixtures shared across modules."""

from __future__ import annotations

import pytest

from tests.unit.file_ticket_support import install_fake_signer


@pytest.fixture
def ticket_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sign and verify file tickets with a local key instead of the Vault seed."""
    install_fake_signer(monkeypatch)
