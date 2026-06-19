"""Composition root for the SMAP Egress Proxy (R12.04).

Reads environment variables via ``EgressProxyEnvConfig`` (pydantic-settings),
wires the AllowlistChecker against the main PostgreSQL database, and exposes
the FastAPI application as ``app`` for uvicorn to serve.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contexts.agents.infrastructure.mcp_tables import mcp_egress_allowlist
from services.egress_proxy.app import AllowlistChecker, EgressProxySettings, create_app
from services.egress_proxy.config import EgressProxyEnvConfig


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
    cfg = EgressProxyEnvConfig()

    engine = create_async_engine(
        cfg.smap_db_dsn,
        pool_size=3,
        max_overflow=2,
        pool_recycle=300,
        pool_pre_ping=True,
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    settings = EgressProxySettings(
        shared_secret=cfg.shared_secret_bytes,
        allowlist_checker=_DbAllowlistChecker(session_factory),
        upstream_timeout_s=cfg.smap_egress_upstream_timeout_s,
    )
    return create_app(settings)


app = _build_app()
