"""Shared harness for db-tier tests of guest-facing routes.

``test_guest_session_hardening_db.py`` and ``test_guest_identity_writes_db.py``
still carry their own copies of these; see the guest-kick-and-ban dossier's
follow-ups.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import messages as messages_route
from contexts.conversation.application import attachment_service as attachment_service_mod
from contexts.conversation.interfaces import error_mapping
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

ACTOR_IP = "203.0.113.7"


async def purge_room_audit(sessionmaker: async_sessionmaker[AsyncSession], chatroom_id: uuid.UUID) -> None:
    """Delete audit rows naming the room, under the retention role.

    Guest and moderation rows carry actors the ``project`` fixture's owner-keyed
    cleanup does not reach.
    """
    async with sessionmaker() as cleanup:
        await cleanup.execute(text("SET ROLE smap_audit_retention"))
        try:
            await cleanup.execute(
                text(
                    "DELETE FROM audit_logs WHERE resource_id = :cid OR metadata->>'chatroom_id' = :cid_text"
                ),
                {"cid": chatroom_id, "cid_text": str(chatroom_id)},
            )
        finally:
            await cleanup.execute(text("RESET ROLE"))
        await cleanup.commit()


def quiet_message_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-commit wake-ups and the storage write are outside what is asserted."""
    for name in (
        "_list_bound_agents_for_dispatch",
        "_dispatch_message_wakeups",
        "_dispatch_graphrag_builds",
        "_dispatch_mention_wakeups",
        "_dispatch_message_workflow_signal",
    ):
        monkeypatch.setattr(messages_route, name, AsyncMock(return_value=None))
    minio = SimpleNamespace(chat_uploads_bucket="chat-uploads", put_object=AsyncMock(), remove=AsyncMock())
    monkeypatch.setattr(attachment_service_mod, "get_minio_client", lambda: minio)
    monkeypatch.setattr(attachment_service_mod, "_enqueue_scan", AsyncMock())
    monkeypatch.setattr(attachment_service_mod, "_enqueue_extraction", AsyncMock())


def route_app(
    sessionmaker: async_sessionmaker[AsyncSession],
    principal: Principal | None,
    routers: Sequence[APIRouter],
    *,
    request_id: uuid.UUID | None = None,
) -> FastAPI:
    """The routers under the production db_session contract (commit on success,
    roll back on error) and the request context AuthMiddleware would stamp."""
    app = FastAPI()
    error_mapping.register(app)
    for router in routers:
        app.include_router(router)

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    ctx = RequestContext(request_id=request_id or uuid.uuid4(), actor_ip=ACTOR_IP, principal=principal)
    app.dependency_overrides[db_session] = _db
    app.dependency_overrides[current_context] = lambda: ctx
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return app


def https_client(app: FastAPI) -> httpx.AsyncClient:
    # https: the refresh cookie may be Secure, and httpx withholds it over http.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def guest_principal(guest_id: uuid.UUID, chatroom_id: uuid.UUID) -> Principal:
    """As ``AuthMiddleware._handle_guest`` builds it: the id is a guest_sessions.id."""
    return Principal(
        user_id=guest_id, is_admin=False, email_verified=False, is_guest=True, chatroom_id=chatroom_id
    )
