"""Redis readiness — PING over the configured DSN."""

from __future__ import annotations

from redis.asyncio import Redis

from app.config.settings import Settings

from .base import ProbeResult


async def probe_redis(settings: Settings) -> ProbeResult:
    client: Redis = Redis.from_url(settings.redis.dsn, socket_connect_timeout=1.5, socket_timeout=1.5)
    try:
        pong = await client.ping()
    finally:
        await client.aclose()
    return ProbeResult("redis", bool(pong), None if pong else "no-pong")
