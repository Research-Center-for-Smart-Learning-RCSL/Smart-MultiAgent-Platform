"""Neo4j readiness — `verify_connectivity()` on a fresh async driver."""

from __future__ import annotations

from neo4j import AsyncGraphDatabase

from app.config.settings import Settings

from .base import ProbeResult


async def probe_neo4j(settings: Settings) -> ProbeResult:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j.url,
        auth=(settings.neo4j.user, settings.neo4j.password),
        connection_timeout=1.5,
    )
    try:
        await driver.verify_connectivity()
    finally:
        await driver.close()
    return ProbeResult("neo4j", True)
