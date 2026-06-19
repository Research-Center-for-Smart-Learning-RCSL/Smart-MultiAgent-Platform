"""Per-tenant advisory snapshot worker (I.8).

Daily job that computes per-Org counts (users, chatrooms, agents, workflows,
storage MB) by reading existing rows. No new tables — honours the "no billing,
no quotas" constraint. Results are cached in Redis for 25 h so the
`GET /api/orgs/{id}/quotas` endpoint can serve them without DB queries.

H23 refactor: cross-context SQL queries have been extracted into
``AdvisoryService``. This module is now a thin orchestrator.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from shared_kernel.db.session import get_sessionmaker


async def daily_org_advisory_snapshot(ctx: dict[str, Any]) -> dict[str, int]:
    """Master cron — iterates all active orgs, computes + caches snapshots.

    Tracks per-org failures and raises ``RuntimeError`` if EVERY eligible org
    failed (so Arq retries / alerts), instead of silently reporting success
    with `orgs_processed=0`.
    """
    from contexts.tenancy.application.advisory_service import AdvisoryService

    sm = get_sessionmaker()
    total_orgs = 0
    failed_orgs: list[str] = []

    async with sm() as session:
        svc = AdvisoryService(session)
        org_ids = await svc.list_active_org_ids()

    eligible = len(org_ids)

    for org_id in org_ids:
        try:
            async with sm() as session:
                svc = AdvisoryService(session)
                snapshot = await svc.compute_org_snapshot(org_id)
                await svc.cache_snapshot(org_id, snapshot)
            total_orgs += 1
        except Exception:
            logger.bind(event="advisory_snapshot_error", org_id=str(org_id)).exception(
                f"advisory snapshot failed for org {org_id}"
            )
            failed_orgs.append(str(org_id))

    logger.bind(
        event="advisory_snapshot_done",
        orgs_processed=total_orgs,
        eligible=eligible,
        failed=len(failed_orgs),
    ).info(f"advisory snapshots computed for {total_orgs}/{eligible} orgs " f"({len(failed_orgs)} failed)")
    _ = ctx

    # If every eligible org blew up, surface to Arq instead of returning quietly.
    if eligible > 0 and len(failed_orgs) == eligible:
        raise RuntimeError(
            f"advisory snapshot: all {eligible} orgs failed " f"(first 5: {', '.join(failed_orgs[:5])})"
        )

    return {
        "orgs_processed": total_orgs,
        "eligible": eligible,
        "failed": len(failed_orgs),
    }


async def get_org_advisory(org_id: object) -> dict[str, Any] | None:
    """Read cached snapshot for an org. Returns None if not yet computed."""
    from contexts.tenancy.application.advisory_service import AdvisoryService

    return await AdvisoryService.get_cached_snapshot(org_id)


async def get_advisory_targets() -> dict[str, int]:
    """Read operator-configurable soft advisory targets from rate_limit_policies."""
    from contexts.tenancy.application.advisory_service import AdvisoryService

    sm = get_sessionmaker()
    async with sm() as session:
        return await AdvisoryService(session).get_advisory_targets()


__all__ = ["daily_org_advisory_snapshot", "get_org_advisory", "get_advisory_targets"]
