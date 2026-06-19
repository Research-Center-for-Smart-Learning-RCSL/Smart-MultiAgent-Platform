"""80% hourly-limit sampler (D.8 / R7.11).

Runs every 30 seconds. Walks every active `key_group_members` row; for each
row whose hourly caps are set, queries `redis_buckets.usage` and — when any
axis exceeds 80 % — emits an in-app notification and records a
`key.usage_threshold_hit` audit event.

Notification delivery goes through Phase I §I.3 (in-app only, no email, no
webhook). The emit helper is stubbed here because I.3 hasn't landed yet —
the call site is real, the sink can be wired without changing this module.

SoC: no FastAPI, no provider calls. The worker is an arq task called from
`app.workers` — same dispatch pattern as existing C-phase workers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.infrastructure import tables as t
from contexts.notification.application.notification_service import NotificationService
from contexts.notification.domain.models import NotificationKind
from shared_kernel import audit
from shared_kernel.auth.clients import get_redis, now
from shared_kernel.infra import redis_buckets
from shared_kernel.observability.metrics import USAGE_THRESHOLD_EVENTS_TOTAL

_THRESHOLD_PCT = 80
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _LimitRow:
    group_id: uuid.UUID
    key_id: uuid.UUID
    project_id: uuid.UUID
    max_input: int | None
    max_output: int | None
    max_requests: int | None


async def _list_limited_members(db: AsyncSession) -> list[_LimitRow]:
    stmt = (
        sa.select(
            t.key_group_members.c.group_id,
            t.key_group_members.c.key_id,
            t.key_groups.c.project_id,
            t.key_group_members.c.max_input_tokens_per_hour,
            t.key_group_members.c.max_output_tokens_per_hour,
            t.key_group_members.c.max_requests_per_hour,
        )
        .select_from(
            t.key_group_members.join(t.key_groups, t.key_group_members.c.group_id == t.key_groups.c.id)
        )
        .where(
            sa.and_(
                t.key_groups.c.deleted_at.is_(None),
                sa.or_(
                    t.key_group_members.c.max_input_tokens_per_hour.isnot(None),
                    t.key_group_members.c.max_output_tokens_per_hour.isnot(None),
                    t.key_group_members.c.max_requests_per_hour.isnot(None),
                ),
            )
        )
    )
    rows = (await db.execute(stmt)).all()
    return [
        _LimitRow(
            group_id=row.group_id,
            key_id=row.key_id,
            project_id=row.project_id,
            max_input=row.max_input_tokens_per_hour,
            max_output=row.max_output_tokens_per_hour,
            max_requests=row.max_requests_per_hour,
        )
        for row in rows
    ]


def _breached_axes(row: _LimitRow, used: redis_buckets.Usage) -> list[str]:
    axes: list[str] = []
    if row.max_input is not None and used.input_tokens * 100 >= row.max_input * _THRESHOLD_PCT:
        axes.append("input_tokens")
    if row.max_output is not None and used.output_tokens * 100 >= row.max_output * _THRESHOLD_PCT:
        axes.append("output_tokens")
    if row.max_requests is not None and used.requests * 100 >= row.max_requests * _THRESHOLD_PCT:
        axes.append("requests")
    return axes


async def _project_member_ids(db: AsyncSession, project_id: uuid.UUID) -> list[uuid.UUID]:
    """Return all user IDs with KEY_VIEW_USAGE_PROJECT capability on the project.

    All org members and direct project members have this capability per §5.2.
    """
    rows = (
        await db.execute(
            sa.text(
                "SELECT DISTINCT user_id FROM project_members WHERE project_id = :pid"
                " UNION"
                " SELECT om.user_id FROM org_members om"
                " JOIN projects p ON p.owner_org_id = om.org_id"
                " WHERE p.id = :pid"
            ).bindparams(pid=project_id)
        )
    ).all()
    return [r[0] for r in rows]


async def sample_once(db: AsyncSession) -> int:
    """Run one sampling pass. Returns the number of threshold hits emitted."""
    redis = get_redis()
    hits = 0
    for row in await _list_limited_members(db):
        used = await redis_buckets.usage(row.key_id)
        axes = _breached_axes(row, used)
        if not axes:
            continue
        hits += 1
        await audit.emit(
            db,
            audit.AuditEvent(
                action="key.usage_threshold_hit",
                resource_type="api_key",
                resource_id=row.key_id,
                metadata={
                    "group_id": str(row.group_id),
                    "project_id": str(row.project_id),
                    "axes": axes,
                    "used_input_tokens": used.input_tokens,
                    "used_output_tokens": used.output_tokens,
                    "used_requests": used.requests,
                    "max_input": row.max_input,
                    "max_output": row.max_output,
                    "max_requests": row.max_requests,
                },
            ),
        )
        # Rate-limit: at most one notification per key per hour (Risks §I.∞).
        hour_slot = now().strftime("%Y%m%d%H")
        dedup_key = f"notif:threshold:{row.key_id}:{hour_slot}"
        if await redis.setnx(dedup_key, "1"):
            await redis.expire(dedup_key, 3600)
            user_ids = await _project_member_ids(db, row.project_id)
            notif_svc = NotificationService(db)
            for uid in user_ids:
                await notif_svc.send(
                    user_id=uid,
                    kind=NotificationKind.KEY_USAGE_THRESHOLD,
                    title="API key usage near limit",
                    metadata={
                        "key_id": str(row.key_id),
                        "group_id": str(row.group_id),
                        "axes": axes,
                    },
                )
        USAGE_THRESHOLD_EVENTS_TOTAL.inc()
        _log.info(
            "key_usage_threshold_hit key=%s group=%s axes=%s",
            row.key_id,
            row.group_id,
            ",".join(axes),
        )
    return hits


__all__ = ["sample_once"]
