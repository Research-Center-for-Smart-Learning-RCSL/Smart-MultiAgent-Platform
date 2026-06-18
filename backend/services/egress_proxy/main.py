"""Composition root for the SMAP Egress Proxy (R12.04).

Reads environment variables, wires the AllowlistChecker against the main
PostgreSQL database, and exposes the FastAPI application as ``app`` for
uvicorn to serve.

Required environment variables:
    EGRESS_PROXY_SHARED_SECRET  — 64 hex chars (32 bytes).
                                   Generate with: openssl rand -hex 32
    SMAP_DB_DSN                 — PostgreSQL async DSN
                                   (asyncpg dialect, e.g.
                                    postgresql+asyncpg://smap:smap@postgres:5432/smap)

Optional:
    SMAP_EGRESS_UPSTREAM_TIMEOUT_S  — upstream forward timeout in seconds
                                       (default: 20.0)
"""

from __future__ import annotations

import os
import uuid

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contexts.agents.infrastructure.mcp_tables import mcp_egress_allowlist
from services.egress_proxy.app import AllowlistChecker, EgressProxySettings, create_app


class _DbAllowlistChecker(AllowlistChecker):
    """AllowlistChecker backed by a short-lived SQLAlchemy async session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def is_allowed(self, *, project_id: uuid.UUID, hostname: str) -> bool:
        async with self._sf() as session:
            row = (
                await session.execute(
                    sa.select(mcp_egress_allowlist.c.id).where(
                        sa.and_(
                            mcp_egress_allowlist.c.project_id == project_id,
                            mcp_egress_allowlist.c.hostname == hostname.lower(),
                        )
                    )
                )
            ).first()
            return row is not None


def _build_app() -> FastAPI:
    secret_hex = os.environ.get("EGRESS_PROXY_SHARED_SECRET", "")
    try:
        shared_secret = bytes.fromhex(secret_hex)
    except ValueError as exc:
        raise ValueError(
            "EGRESS_PROXY_SHARED_SECRET must be a hex string" " (e.g. 64 hex chars = 32 bytes)"
        ) from exc
    if len(shared_secret) < 32:
        raise ValueError(
            "EGRESS_PROXY_SHARED_SECRET must be at least 32 bytes"
            " (64 hex chars). Generate with: openssl rand -hex 32"
        )

    db_dsn = os.environ.get("SMAP_DB_DSN", "")
    if not db_dsn:
        raise ValueError("SMAP_DB_DSN is required")

    timeout = float(os.environ.get("SMAP_EGRESS_UPSTREAM_TIMEOUT_S", "20.0"))

    engine = create_async_engine(
        db_dsn,
        pool_size=3,
        max_overflow=2,
        pool_recycle=300,
        pool_pre_ping=True,
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    settings = EgressProxySettings(
        shared_secret=shared_secret,
        allowlist_checker=_DbAllowlistChecker(session_factory),
        upstream_timeout_s=timeout,
    )
    return create_app(settings)


app = _build_app()
