"""Global test fixtures. Phase A scope: TestClient for the FastAPI app."""

from __future__ import annotations

import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
import shared_kernel.auth.ip_bans as _ip_bans


async def _fake_ip_ban_reload(_session) -> None:  # type: ignore[no-untyped-def]
    """No-op reload: keeps cache fresh so the middleware skips DB calls."""
    _ip_bans._cache.networks = []
    _ip_bans._cache.loaded_at = time.monotonic()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with patch("shared_kernel.auth.ip_bans.reload", new=_fake_ip_ban_reload):
        with TestClient(create_app()) as c:
            yield c
