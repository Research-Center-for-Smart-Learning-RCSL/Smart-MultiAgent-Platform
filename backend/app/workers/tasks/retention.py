"""Comprehensive retention workers (I.4).

Each policy is a separate async function called by the master
`retention_sweep` cron. Workers are idempotent, Redis-locked, and emit
an audit summary with rows_affected.
"""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import tempfile
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.storage import get_minio_client


async def _emit_summary(
    session: AsyncSession, action: str, rows_affected: int
) -> None:
    if rows_affected > 0:
        await audit.emit(
            session,
            audit.AuditEvent(
                action=action,
                metadata={"rows_affected": rows_affected},
            ),
        )


async def _purge_messages(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=5 * 365)
    result = await session.execute(
        sa.text(
            "DELETE FROM messages WHERE created_at < :cutoff "
            "AND id IN (SELECT id FROM messages WHERE created_at < :cutoff LIMIT 1000)"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.messages.swept", count)
    return count


async def _purge_message_attachments(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=3)
    result = await session.execute(
        sa.text(
            "DELETE FROM message_attachments "
            "WHERE expires_at IS NOT NULL AND expires_at < :cutoff "
            "AND message_id IS NULL "
            "AND id IN ("
            "  SELECT id FROM message_attachments "
            "  WHERE expires_at IS NOT NULL AND expires_at < :cutoff "
            "  AND message_id IS NULL "
            "  LIMIT 500"
            ")"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.message_attachments.swept", count)
    return count


async def _purge_audit_logs(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=365)
    # Must SET ROLE to smap_audit_retention to bypass the append-only trigger.
    await session.execute(sa.text("SET ROLE smap_audit_retention"))
    try:
        result = await session.execute(
            sa.text(
                "DELETE FROM audit_logs WHERE created_at < :cutoff "
                "AND id IN (SELECT id FROM audit_logs WHERE created_at < :cutoff LIMIT 1000)"
            ).bindparams(cutoff=cutoff)
        )
        count = result.rowcount or 0
    finally:
        await session.execute(sa.text("RESET ROLE"))
    return count


async def _archive_workflow_runs(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=90)
    result = await session.execute(
        sa.text(
            "INSERT INTO workflow_runs_archive "
            "(id, workflow_id, state, started_at, ended_at) "
            "SELECT id, workflow_id, state, started_at, ended_at "
            "FROM workflow_runs WHERE ended_at IS NOT NULL AND ended_at < :cutoff "
            "AND id NOT IN (SELECT id FROM workflow_runs_archive) "
            "LIMIT 500"
        ).bindparams(cutoff=cutoff)
    )
    archived = result.rowcount or 0
    if archived > 0:
        await session.execute(
            sa.text(
                "DELETE FROM workflow_steps WHERE run_id IN "
                "(SELECT id FROM workflow_runs WHERE ended_at IS NOT NULL "
                "AND ended_at < :cutoff LIMIT 500)"
            ).bindparams(cutoff=cutoff)
        )
        # PostgreSQL does not support DELETE...LIMIT; use a subquery join.
        await session.execute(
            sa.text(
                "DELETE FROM workflow_runs WHERE id IN ("
                "  SELECT wr.id FROM workflow_runs wr"
                "  JOIN workflow_runs_archive wra ON wra.id = wr.id"
                "  WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff"
                "  LIMIT 500"
                ")"
            ).bindparams(cutoff=cutoff)
        )
    await _emit_summary(session, "retention.workflow_runs.archived", archived)
    return archived


async def _rollup_key_usage_events(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=13 * 30)
    result = await session.execute(
        sa.text(
            "WITH old AS ("
            "  SELECT id, key_id, date_trunc('day', at) AS day, "
            "         input_tokens, output_tokens "
            "  FROM key_usage_events WHERE at < :cutoff LIMIT 1000"
            "), agg AS ("
            "  SELECT key_id, day, count(*) AS req_count, "
            "         sum(input_tokens) AS sum_input, "
            "         sum(output_tokens) AS sum_output "
            "  FROM old GROUP BY key_id, day"
            "), upserted AS ("
            "  INSERT INTO key_usage_daily "
            "  (key_id, day, requests, input_tokens, output_tokens) "
            "  SELECT key_id, day, req_count, sum_input, sum_output "
            "  FROM agg "
            "  ON CONFLICT (key_id, day) DO UPDATE SET "
            "    requests = key_usage_daily.requests + EXCLUDED.requests, "
            "    input_tokens = key_usage_daily.input_tokens + EXCLUDED.input_tokens, "
            "    output_tokens = key_usage_daily.output_tokens + EXCLUDED.output_tokens "
            "  RETURNING 1"
            ") "
            "DELETE FROM key_usage_events WHERE id IN (SELECT id FROM old)"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.key_usage_events.rolled_up", count)
    return count


async def _purge_soft_deleted_tenancy(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=60)
    total = 0
    for tbl in ("orgs", "projects", "agents", "workflows", "chatrooms"):
        result = await session.execute(
            sa.text(
                f"DELETE FROM {tbl} WHERE deleted_at IS NOT NULL "
                f"AND deleted_at < :cutoff "
                f"AND id IN (SELECT id FROM {tbl} WHERE deleted_at IS NOT NULL "
                f"AND deleted_at < :cutoff LIMIT 200)"
            ).bindparams(cutoff=cutoff)
        )
        total += result.rowcount or 0
    await _emit_summary(session, "retention.soft_deleted.swept", total)
    return total


async def _expire_invites(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text(
            "UPDATE invites SET state = 'expired' "
            "WHERE state = 'pending' AND expires_at < now()"
        )
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.invites.expired", count)
    return count


async def _expire_oc_transfers(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text(
            "UPDATE original_creator_transfers SET state = 'expired' "
            "WHERE state = 'pending' AND resolved_at IS NULL AND expires_at < now()"
        )
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.oc_transfers.expired", count)
    return count


async def _expire_approvals(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text(
            "UPDATE approvals SET state = 'timeout_leader' "
            "WHERE state = 'pending' "
            "AND started_at + make_interval(secs => timeout_seconds) < now()"
        )
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.approvals.expired", count)
    return count


async def _purge_expired_tokens(session: AsyncSession) -> int:
    total = 0
    for tbl in ("email_verify_tokens", "password_reset_tokens"):
        result = await session.execute(
            sa.text(f"DELETE FROM {tbl} WHERE expires_at < now()")
        )
        total += result.rowcount or 0
    await _emit_summary(session, "retention.tokens.swept", total)
    return total


async def _prune_idle_sessions(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=30)
    result = await session.execute(
        sa.text(
            "DELETE FROM sessions WHERE last_used_at < :cutoff "
            "AND id IN (SELECT id FROM sessions WHERE last_used_at < :cutoff LIMIT 1000)"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.sessions.pruned", count)
    return count


async def _purge_agent_instances(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=30)
    result = await session.execute(
        sa.text(
            "DELETE FROM agent_instances "
            "WHERE destroyed_at IS NOT NULL AND destroyed_at < :cutoff "
            "AND id IN ("
            "  SELECT id FROM agent_instances "
            "  WHERE destroyed_at IS NOT NULL AND destroyed_at < :cutoff LIMIT 500"
            ")"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.agent_instances.swept", count)
    return count


async def _close_idle_impersonations(session: AsyncSession) -> int:
    cutoff = now() - timedelta(minutes=30)
    result = await session.execute(
        sa.text(
            "UPDATE admin_impersonation_sessions SET ended_at = now() "
            "WHERE ended_at IS NULL AND started_at < :cutoff"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.impersonations.closed", count)
    return count


async def _purge_exports_bucket(session: AsyncSession) -> int:
    """Delete MinIO export objects older than 24 h (§21.5)."""
    mc = get_minio_client()
    cutoff = now()
    cutoff_ts = (cutoff - timedelta(hours=24)).timestamp()

    def _sweep() -> int:
        removed = 0
        try:
            for obj in mc._client.list_objects(mc.exports_bucket, recursive=True):
                lm = obj.last_modified
                if lm is None:
                    continue
                if lm.timestamp() < cutoff_ts:
                    try:
                        mc._client.remove_object(mc.exports_bucket, obj.object_name)
                        removed += 1
                    except Exception:  # noqa: BLE001 — best-effort per object
                        pass
        except Exception:  # noqa: BLE001 — bucket may not exist yet
            pass
        return removed

    count = await asyncio.to_thread(_sweep)
    await _emit_summary(session, "retention.exports_bucket.swept", count)
    return count


async def _sweep_instructions_chains(session: AsyncSession) -> int:
    """Hard-delete instruction chains whose every row reached a terminal state
    and whose most recent activity is older than the audit retention window
    (365 d per R17.01 / §21.1).
    """
    cutoff = now() - timedelta(days=365)
    result = await session.execute(
        sa.text(
            "DELETE FROM instructions WHERE id IN ("
            "  SELECT id FROM instructions WHERE chain_id IN ("
            "    SELECT chain_id FROM instructions"
            "    GROUP BY chain_id"
            "    HAVING bool_and(state IN ('completed','rejected_loop','timeout'))"
            "       AND max(coalesce(resolved_at, issued_at)) < :cutoff"
            "  ) LIMIT 500"
            ")"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0
    await _emit_summary(session, "retention.instructions_chains.swept", count)
    return count


async def _cleanup_tus_parts(session: AsyncSession) -> int:
    """Remove abandoned TUS `.part` staging files older than 24 h (R22.15.04).

    Redis TTL already reclaims the metadata; this job reclaims on-disk bytes.
    """
    staging_dir = os.environ.get("SMAP_TUS_STAGING_DIR") or os.path.join(
        tempfile.gettempdir(), "smap-tus"
    )
    cutoff_ts = (now() - timedelta(hours=24)).timestamp()
    count = 0
    try:
        pattern = os.path.join(staging_dir, "*.part")
        for path in _glob.glob(pattern):
            try:
                if os.path.getmtime(path) < cutoff_ts:
                    os.remove(path)
                    count += 1
            except OSError:
                pass
    except OSError:
        pass
    await _emit_summary(session, "retention.tus_parts.swept", count)
    return count


_POLICIES = [
    ("messages", _purge_messages),
    ("message_attachments", _purge_message_attachments),
    ("audit_logs", _purge_audit_logs),
    ("workflow_runs", _archive_workflow_runs),
    ("key_usage_events", _rollup_key_usage_events),
    ("soft_deleted", _purge_soft_deleted_tenancy),
    ("invites", _expire_invites),
    ("oc_transfers", _expire_oc_transfers),
    ("approvals", _expire_approvals),
    ("tokens", _purge_expired_tokens),
    ("sessions", _prune_idle_sessions),
    ("agent_instances", _purge_agent_instances),
    ("impersonations", _close_idle_impersonations),
    ("exports_bucket", _purge_exports_bucket),
    ("instructions_chains", _sweep_instructions_chains),
    ("tus_parts", _cleanup_tus_parts),
]


async def retention_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    """Master retention cron — runs all policies in sequence."""
    sm = get_sessionmaker()
    report: dict[str, int] = {}
    for name, func in _POLICIES:
        try:
            async with sm() as session:
                async with session.begin():
                    count = await func(session)
            report[name] = count
        except Exception:
            logger.bind(event=f"retention_{name}_error").exception(
                f"retention policy {name} failed"
            )
            report[name] = -1
    total = sum(v for v in report.values() if v > 0)
    logger.bind(event="retention_sweep_done", report=report).info(
        f"retention sweep complete — {total} total rows affected"
    )
    _ = ctx
    return report


__all__ = ["retention_sweep"]
