"""Global test fixtures. Phase A scope: TestClient for the FastAPI app."""

from __future__ import annotations

import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.bootstrap.startup as _startup
import shared_kernel.auth.ip_bans as _ip_bans
from app.main import create_app


async def _fake_ip_ban_reload(_session) -> None:  # type: ignore[no-untyped-def]
    """No-op reload: keeps cache fresh so the middleware skips DB calls."""
    _ip_bans._cache.networks = []
    _ip_bans._cache.loaded_at = time.monotonic()


#: Startup steps the unit tier cannot run: they need real infrastructure and are
#: deliberately fatal when it is absent.
#:
#: `import_email_domain_policy_step` (R19a.13) reads Redis and writes PostgreSQL.
#: Unlike the rate-limit primer beside it, an email-domain policy has no
#: compile-time default to fall back on, so a boot that continued past an
#: unreadable one would serve requests with no policy authority at all. That is
#: the right production behaviour, and the reason this fixture removes the step
#: rather than the step being softened. Its logic is covered by
#: `tests/unit/test_email_domain_policy_admin_api.py` and, against real stores,
#: by `tests/integration/test_email_domain_policy_db.py`.
_SKIPPED_INITIALIZERS = frozenset({"import_email_domain_policy_step"})

# `app.main` binds INITIALIZERS by value at import, and the list holds function
# objects — so patching the step's name in `app.bootstrap.startup` would not
# change what the lifespan iterates. Replace the list `app.main` actually reads.
_UNIT_TIER_INITIALIZERS = [
    step for step in _startup.INITIALIZERS if step.__name__ not in _SKIPPED_INITIALIZERS
]


@pytest.fixture
def client() -> Iterator[TestClient]:
    with (
        patch("shared_kernel.auth.ip_bans.reload", new=_fake_ip_ban_reload),
        patch("app.main.INITIALIZERS", new=_UNIT_TIER_INITIALIZERS),
        TestClient(create_app()) as c,
    ):
        yield c
