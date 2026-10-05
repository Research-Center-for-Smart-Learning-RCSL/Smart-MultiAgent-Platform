"""``require_registered_principal`` and the three routes it guards -- AC-6.

Spec: ``docs/tasks/2026-10-05-guest-identity-foreign-keys/spec.md`` §7.5.

An anonymous guest's principal reaches every route that depends only on
``current_principal``. These three write the principal id into a column that
references ``users`` (or start a flow that does), so a guest must be refused
before the handler runs: the key upload otherwise probes the provider and
encrypts the secret through Vault for an anonymous caller first.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1 import auth as auth_route
from app.api.v1 import guests as guests_route
from app.api.v1 import keys as keys_route
from shared_kernel.auth.dependencies import current_principal, require_registered_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

_ROOM = uuid.uuid4()
_GUEST = Principal(
    user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=_ROOM
)
_USER = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


class TestDependency:
    async def test_a_registered_user_passes_through(self) -> None:
        assert await require_registered_principal(_USER) is _USER

    async def test_a_guest_is_refused_with_a_dedicated_problem(self) -> None:
        """403, not 401: the client treats an authenticated 401 as a cue to
        refresh, and with guest context set it would refresh the guest token and
        replay the request (``axios.ts`` interceptor)."""
        with pytest.raises(HTTPException) as caught:
            await require_registered_principal(_GUEST)
        assert caught.value.status_code == 403
        assert caught.value.detail["type"].endswith("/auth/registered-account-required")


def _app(*routers: object, principal: Principal) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)  # type: ignore[arg-type]

    async def _db() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    app.dependency_overrides[db_session] = _db
    app.dependency_overrides[current_principal] = lambda: principal
    return app


async def _post(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, **kwargs)  # type: ignore[arg-type]


class TestGuardedRoutes:
    async def test_legacy_enroll_refuses_a_guest_before_writing(self) -> None:
        with patch.object(guests_route, "GuestService") as service_cls:
            service_cls.return_value.enroll = AsyncMock()
            response = await _post(
                _app(guests_route.router, principal=_GUEST),
                f"/api/guest/{_ROOM}/{'t' * 32}/enroll",
                json={},
            )
        assert response.status_code == 403
        service_cls.return_value.enroll.assert_not_awaited()

    async def test_key_upload_refuses_a_guest_before_probing(self) -> None:
        with patch.object(keys_route, "KeyService") as service_cls:
            service_cls.return_value.upload = AsyncMock()
            response = await _post(
                _app(keys_route.router, principal=_GUEST),
                "/api/keys",
                json={"provider": "openai", "name": "k", "secret": "sk-test"},
            )
        assert response.status_code == 403
        service_cls.return_value.upload.assert_not_awaited()

    async def test_google_link_start_refuses_a_guest(self) -> None:
        service = MagicMock()
        service.start_google_link = AsyncMock(return_value=("https://example.invalid", "state"))
        with patch.object(auth_route, "_service", return_value=service):
            response = await _post(
                _app(auth_route.router, principal=_GUEST),
                "/api/auth/google/link/start",
            )
        assert response.status_code == 403
        service.start_google_link.assert_not_awaited()

    async def test_a_registered_user_still_reaches_legacy_enroll(self) -> None:
        with patch.object(guests_route, "GuestService") as service_cls:
            service_cls.return_value.enroll = AsyncMock()
            response = await _post(
                _app(guests_route.router, principal=_USER),
                f"/api/guest/{_ROOM}/{'t' * 32}/enroll",
                json={},
            )
        assert response.status_code == 204
        service_cls.return_value.enroll.assert_awaited_once()
