"""Per-tenant advisory snapshot service (I.8).

Encapsulates the raw-SQL snapshot queries that were previously embedded in
the ``advisory`` worker task. The worker task remains a thin orchestrator
that iterates orgs and caches results; the actual computation lives here.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.auth.clients import get_redis, now

_CACHE_PREFIX = "advisory:org:"
_CACHE_TTL_SECONDS = 25 * 3600


class AdvisoryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def compute_org_snapshot(self, org_id: object) -> dict[str, int]:
        """Compute per-org resource counts (users, chatrooms, agents, etc.)."""
        counts: dict[str, int] = {}
        for label, query in (
            ("users", "SELECT count(*) FROM org_members WHERE org_id = :oid"),
            (
                "chatrooms",
                (
                    "SELECT count(*) FROM chatrooms c "
                    "JOIN workspaces w ON w.id = c.workspace_id "
                    "JOIN projects p ON p.id = w.project_id "
                    "WHERE p.owner_org_id = :oid AND c.deleted_at IS NULL"
                ),
            ),
            (
                "agents",
                (
                    "SELECT count(*) FROM agents a "
                    "JOIN projects p ON p.id = a.project_id "
                    "WHERE p.owner_org_id = :oid AND a.deleted_at IS NULL"
                ),
            ),
            (
                "workflows",
                (
                    "SELECT count(*) FROM workflows wf "
                    "JOIN workspaces w ON w.id = wf.workspace_id "
                    "JOIN projects p ON p.id = w.project_id "
                    "WHERE p.owner_org_id = :oid AND wf.deleted_at IS NULL"
                ),
            ),
            (
                "projects",
                "SELECT count(*) FROM projects WHERE owner_org_id = :oid AND deleted_at IS NULL",
            ),
        ):
            result = await self._db.execute(sa.text(query).bindparams(oid=org_id))
            counts[label] = result.scalar_one()
        return counts

    async def list_active_org_ids(self) -> list[Any]:
        """Return all active (non-deleted) org ids."""
        rows = (await self._db.execute(sa.text("SELECT id FROM orgs WHERE deleted_at IS NULL"))).all()
        return [r[0] for r in rows]

    async def cache_snapshot(self, org_id: object, snapshot: dict[str, Any]) -> None:
        """Store a computed snapshot in Redis with 25 h TTL."""
        redis = get_redis()
        snapshot["computed_at"] = now().isoformat()
        await redis.setex(
            f"{_CACHE_PREFIX}{org_id}",
            _CACHE_TTL_SECONDS,
            json.dumps(snapshot),
        )

    @staticmethod
    async def get_cached_snapshot(org_id: object) -> dict[str, Any] | None:
        """Read cached snapshot for an org. Returns None if not yet computed."""
        redis = get_redis()
        raw = await redis.get(f"{_CACHE_PREFIX}{org_id}")
        if raw is None:
            return None
        return json.loads(raw)  # type: ignore[no-any-return]

    async def get_advisory_targets(self) -> dict[str, int]:
        """Read operator-configurable soft advisory targets from rate_limit_policies."""
        rows = (
            await self._db.execute(
                sa.text("SELECT key, max_count FROM rate_limit_policies " "WHERE key LIKE 'advisory_%'")
            )
        ).all()
        targets: dict[str, int] = {}
        for key, max_count in rows:
            label = key.removeprefix("advisory_")
            targets[label] = max_count
        return targets


__all__ = ["AdvisoryService"]
