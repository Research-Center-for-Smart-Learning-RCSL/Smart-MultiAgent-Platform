"""Per-tenant advisory snapshot worker (I.8).

Daily job that computes per-Org counts (users, chatrooms, agents, workflows,
storage MB) by reading existing rows. No new tables — honours the "no billing,
no quotas" constraint. Results are cached in Redis for 25 h so the
`GET /api/orgs/{id}/quotas` endpoint can serve them without DB queries.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from loguru import logger

from shared_kernel.auth.clients import get_redis, now
from shared_kernel.db.session import get_sessionmaker

_CACHE_PREFIX = "advisory:org:"
_CACHE_TTL_SECONDS = 25 * 3600


async def _compute_org_snapshot(session, org_id) -> dict[str, int]:  # type: ignore[no-untyped-def]
    counts: dict[str, int] = {}
    for label, query in (
        ("users", "SELECT count(*) FROM org_members WHERE org_id = :oid"),
        ("chatrooms", (
            "SELECT count(*) FROM chatrooms c "
            "JOIN workspaces w ON w.id = c.workspace_id "
            "JOIN projects p ON p.id = w.project_id "
            "WHERE p.owner_org_id = :oid AND c.deleted_at IS NULL"
        )),
        ("agents", (
            "SELECT count(*) FROM agents a "
            "JOIN projects p ON p.id = a.project_id "
            "WHERE p.owner_org_id = :oid AND a.deleted_at IS NULL"
        )),
        ("workflows", (
            "SELECT count(*) FROM workflows wf "
            "JOIN workspaces w ON w.id = wf.workspace_id "
            "JOIN projects p ON p.id = w.project_id "
            "WHERE p.owner_org_id = :oid AND wf.deleted_at IS NULL"
        )),
        ("projects", (
            "SELECT count(*) FROM projects "
            "WHERE owner_org_id = :oid AND deleted_at IS NULL"
        )),
    ):
        result = await session.execute(sa.text(query).bindparams(oid=org_id))
        counts[label] = result.scalar_one()
    return counts


async def daily_org_advisory_snapshot(ctx: dict[str, Any]) -> dict[str, int]:
    """Master cron — iterates all active orgs, computes + caches snapshots."""
    sm = get_sessionmaker()
    redis = get_redis()
    total_orgs = 0

    async with sm() as session:
        org_rows = (await session.execute(
            sa.text("SELECT id FROM orgs WHERE deleted_at IS NULL")
        )).all()

    for (org_id,) in org_rows:
        try:
            async with sm() as session:
                snapshot = await _compute_org_snapshot(session, org_id)
            import json
            snapshot["computed_at"] = now().isoformat()
            await redis.setex(
                f"{_CACHE_PREFIX}{org_id}",
                _CACHE_TTL_SECONDS,
                json.dumps(snapshot),
            )
            total_orgs += 1
        except Exception:
            logger.bind(event="advisory_snapshot_error", org_id=str(org_id)).exception(
                f"advisory snapshot failed for org {org_id}"
            )

    logger.bind(event="advisory_snapshot_done", orgs_processed=total_orgs).info(
        f"advisory snapshots computed for {total_orgs} orgs"
    )
    _ = ctx
    return {"orgs_processed": total_orgs}


async def get_org_advisory(org_id) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    """Read cached snapshot for an org. Returns None if not yet computed."""
    import json
    redis = get_redis()
    raw = await redis.get(f"{_CACHE_PREFIX}{org_id}")
    if raw is None:
        return None
    return json.loads(raw)


async def get_advisory_targets() -> dict[str, int]:
    """Read operator-configurable soft advisory targets from rate_limit_policies."""
    sm = get_sessionmaker()
    targets: dict[str, int] = {}
    async with sm() as session:
        rows = (await session.execute(
            sa.text(
                "SELECT key, max_count FROM rate_limit_policies "
                "WHERE key LIKE 'advisory_%'"
            )
        )).all()
    for key, max_count in rows:
        label = key.removeprefix("advisory_")
        targets[label] = max_count
    return targets


__all__ = ["daily_org_advisory_snapshot", "get_org_advisory", "get_advisory_targets"]
