"""Comprehensive retention workers (I.4).

Each policy is a separate async function called by the master
`retention_sweep` cron. Workers are idempotent, Redis-locked, and emit
an audit summary with rows_affected.

H4 refactor: cross-context raw SQL has been extracted into each context's
facade or service. This module is a thin orchestrator.
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

from contexts.agents.infrastructure.tables import agents as agents_tbl
from contexts.conversation.application.retention_service import RetentionService
from contexts.conversation.infrastructure.tables import chatrooms as chatrooms_tbl
from contexts.identity.infrastructure.tables import (
    email_verify_tokens as email_verify_tokens_tbl,
)
from contexts.identity.infrastructure.tables import (
    password_reset_tokens as password_reset_tokens_tbl,
)
from contexts.tenancy.infrastructure.tables import (
    orgs as orgs_tbl,
)
from contexts.tenancy.infrastructure.tables import (
    projects as projects_tbl,
)
from contexts.workflow.infrastructure.tables import workflows as workflows_tbl
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.observability.metrics import (
    ADMIN_IMPERSONATION_SESSIONS_ACTIVE,
    RETENTION_FAILURES,
    RETENTION_LAST_ROWS,
    RETENTION_LAST_RUN_TIMESTAMP,
)
from shared_kernel.storage import get_minio_client

_SOFT_DELETE_TABLES: tuple[sa.Table, ...] = (
    orgs_tbl,
    projects_tbl,
    agents_tbl,
    workflows_tbl,
    chatrooms_tbl,
)
_TOKEN_TABLES: tuple[sa.Table, ...] = (
    email_verify_tokens_tbl,
    password_reset_tokens_tbl,
)


async def _emit_summary(session: AsyncSession, action: str, rows_affected: int) -> None:
    await audit.emit(
        session,
        audit.AuditEvent(
            action=action,
            metadata={"rows_affected": rows_affected},
        ),
    )


async def _purge_messages(session: AsyncSession) -> int:
    """Hard-delete messages past the 5-year horizon (R13.15 / F.8).

    Delegates to ``RetentionService.purge_once`` so the MinIO objects backing
    attachments of purged messages are removed in the same pass — a plain
    ``DELETE`` would orphan them. Chunked with its own short-lived
    transactions so a large backlog cannot hold one giant transaction open.
    """
    sm = get_sessionmaker()
    total_messages = 0
    total_objects = 0
    for _ in range(100):
        async with sm() as chunk, chunk.begin():
            report = await RetentionService(chunk).purge_once()
        if report.messages_deleted == 0:
            break
        total_messages += report.messages_deleted
        total_objects += report.attachments_objects_removed
    await audit.emit(
        session,
        audit.AuditEvent(
            action="retention.messages.swept",
            metadata={
                "rows_affected": total_messages,
                "attachment_objects_removed": total_objects,
            },
        ),
    )
    return total_messages


async def _purge_message_attachments(session: AsyncSession) -> int:
    """Delete orphaned message_attachments via ConversationFacade."""
    from contexts.conversation.interfaces.facade import ConversationFacade

    count = await ConversationFacade(session).purge_old_attachments(max_age_days=3)
    await _emit_summary(session, "retention.message_attachments.swept", count)
    return count


async def _purge_audit_logs(session: AsyncSession) -> int:
    """Delete audit_logs older than 365 days via AuditFacade."""
    from contexts.audit.interfaces.facade import AuditFacade

    count = await AuditFacade(session).purge_old_logs(retention_days=365)
    await _emit_summary(session, "retention.audit_logs.swept", count)
    return count


async def _archive_workflow_runs(session: AsyncSession) -> int:
    """Archive workflow runs ended > 90 days ago via WorkflowFacade."""
    from contexts.workflow.interfaces.facade import WorkflowFacade

    archived = await WorkflowFacade(session).archive_old_runs(retention_days=90)
    await _emit_summary(session, "retention.workflow_runs.archived", archived)
    return archived


async def _rollup_key_usage_events(session: AsyncSession) -> int:
    """Roll up old key_usage_events into daily aggregates via KeysFacade."""
    from contexts.keys.interfaces.facade import KeysFacade

    count = await KeysFacade(session).rollup_usage_events(retention_months=13)
    await _emit_summary(session, "retention.key_usage_events.rolled_up", count)
    return count


async def _purge_soft_deleted_tenancy(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=60)
    total = 0
    for tbl in _SOFT_DELETE_TABLES:
        batch = (
            sa.select(tbl.c.id)
            .where(tbl.c.deleted_at.is_not(None))
            .where(tbl.c.deleted_at < cutoff)
            .limit(200)
        )
        result = await session.execute(
            sa.delete(tbl)
            .where(tbl.c.deleted_at.is_not(None))
            .where(tbl.c.deleted_at < cutoff)
            .where(tbl.c.id.in_(batch))
        )
        total += result.rowcount or 0
    await _emit_summary(session, "retention.soft_deleted.swept", total)
    return total


async def _expire_invites(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text("UPDATE invites SET state = 'expired' " "WHERE state = 'pending' AND expires_at < now()")
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.invites.expired", count)
    return count


async def _expire_oc_transfers(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text(
            "UPDATE original_creator_transfers SET state = 'expired' "
            "WHERE state = 'pending' AND resolved_at IS NULL AND expires_at < now()"
        )
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.approvals.expired", count)
    return count


async def _purge_expired_tokens(session: AsyncSession) -> int:
    total = 0
    for tbl in _TOKEN_TABLES:
        result = await session.execute(sa.delete(tbl).where(tbl.c.expires_at < sa.func.now()))
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.agent_instances.swept", count)
    return count


async def _sweep_orphaned_subagent_roots(session: AsyncSession) -> int:
    """Reap synthetic subagent-root instances — and their children — for
    workflow runs that no longer exist (ASYNC-3).

    ``SubagentService.ensure_root_instance`` creates one depth-0 root
    ``agent_instances`` row per (agent, workflow run) so a workflow
    ``subagent_spawn`` node has a parent instance to spawn under. Neither the
    synthetic root nor its workflow-spawned children are ever destroyed, so
    ``destroyed_at`` stays NULL and ``_purge_agent_instances`` (destroyed-only)
    never reclaims them. Once the owning workflow run is gone — completed and
    archived by ``_archive_workflow_runs``, or hard-deleted — the whole
    synthetic subtree is dead weight.

    Children are deleted before roots: ``agent_instances.parent_id`` is
    ``ON DELETE SET NULL``, so deleting a root first would merely orphan the
    children (``parent_id`` → NULL) and leak them as parentless rows.
    """
    root_rows = await session.execute(
        sa.text(
            "WITH synth AS ("
            "  SELECT id, run_context->>'workflow_run_id' AS wf_run_id "
            "  FROM agent_instances "
            "  WHERE parent_id IS NULL "
            "    AND run_context->>'synthetic_root' = 'true' "
            "  LIMIT 500"
            ") "
            "SELECT s.id FROM synth s "
            "WHERE s.wf_run_id IS NOT NULL "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM workflow_runs wr WHERE wr.id = s.wf_run_id::uuid"
            "  )"
        )
    )
    root_ids = [r[0] for r in root_rows.all()]
    if not root_ids:
        await _emit_summary(session, "retention.subagent_roots.swept", 0)
        return 0
    # Children (depth 1; R15.19 forbids deeper) first — see docstring.
    await session.execute(
        sa.text("DELETE FROM agent_instances WHERE parent_id IN :ids").bindparams(
            sa.bindparam("ids", value=root_ids, expanding=True)
        )
    )
    result = await session.execute(
        sa.text("DELETE FROM agent_instances WHERE id IN :ids").bindparams(
            sa.bindparam("ids", value=root_ids, expanding=True)
        )
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.subagent_roots.swept", count)
    return count


async def _close_idle_impersonations(session: AsyncSession) -> int:
    from shared_kernel.auth import tokens

    cutoff = now() - timedelta(minutes=30)
    result = await session.execute(
        sa.text(
            "UPDATE admin_impersonation_sessions SET ended_at = now() "
            "WHERE ended_at IS NULL AND started_at < :cutoff "
            "RETURNING access_jti"
        ).bindparams(cutoff=cutoff)
    )
    rows = result.all()
    count = len(rows)
    for row in rows:
        if row.access_jti is not None:
            await tokens.deny_access_jti(row.access_jti)
    # Re-sample the gauge after the sweep so dashboards reflect the post-close
    # value. Coarse (nightly) but cheap; fine-grained tracking would belong in
    # the impersonation start/end paths.
    active_row = await session.execute(
        sa.text("SELECT count(*) FROM admin_impersonation_sessions " "WHERE ended_at IS NULL")
    )
    active = int(active_row.scalar() or 0)
    ADMIN_IMPERSONATION_SESSIONS_ACTIVE.set(active)
    await _emit_summary(session, "retention.impersonations.closed", count)
    return count


async def _purge_exports_bucket(session: AsyncSession) -> int:
    """Delete MinIO export objects older than 24 h (section 21.5).

    Per-object delete is retried with exponential backoff (3 attempts, base 0.5 s)
    so a transient network blip doesn't strand objects until the next sweep.

    Kept in retention (infra concern) — not context-specific.
    """
    import time as _time

    mc = get_minio_client()
    cutoff = now()
    cutoff_ts = (cutoff - timedelta(hours=24)).timestamp()

    def _sweep() -> tuple[int, int]:
        removed = 0
        failed = 0
        try:
            objects = mc.list_objects_sync(mc.exports_bucket)
        except Exception:
            logger.bind(bucket=mc.exports_bucket).exception("exports_bucket sweep: list_objects failed")
            return (0, 0)

        for obj in objects:
            lm = obj.last_modified
            if lm is None:
                continue
            if lm.timestamp() >= cutoff_ts:
                continue

            for attempt in range(3):
                try:
                    mc.remove_object_sync(mc.exports_bucket, obj.object_name)
                    removed += 1
                    break
                except Exception as exc:
                    if attempt == 2:
                        failed += 1
                        logger.bind(
                            bucket=mc.exports_bucket,
                            object_name=obj.object_name,
                            error=str(exc),
                        ).warning("exports_bucket sweep: remove failed after 3 attempts")
                    else:
                        _time.sleep(0.5 * (2**attempt))
        return (removed, failed)

    removed, failed = await asyncio.to_thread(_sweep)
    if failed:
        logger.bind(removed=removed, failed=failed).warning(
            f"exports_bucket sweep: {failed} objects could not be removed"
        )
    await _emit_summary(session, "retention.exports_bucket.swept", removed)
    return removed


async def _sweep_instructions_chains(session: AsyncSession) -> int:
    """Hard-delete instruction chains whose every row reached a terminal state
    and whose most recent activity is older than the audit retention window
    (365 d per R17.01 / section 21.1).
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.instructions_chains.swept", count)
    return count


async def _manage_key_usage_partitions(session: AsyncSession) -> int:
    """Monthly-range partition manager for ``key_usage_events`` via KeysFacade."""
    from contexts.keys.interfaces.facade import KeysFacade

    total = await KeysFacade(session).manage_partitions()
    await _emit_summary(session, "retention.key_usage_partitions.managed", total)
    return total


async def _cleanup_tus_parts(session: AsyncSession) -> int:
    """Remove abandoned TUS `.part` staging files older than 24 h (R22.15.04).

    Redis TTL already reclaims the metadata; this job reclaims on-disk bytes.
    """
    staging_dir = os.environ.get("SMAP_TUS_STAGING_DIR") or os.path.join(tempfile.gettempdir(), "smap-tus")
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


async def _scrub_stale_presence(session: AsyncSession) -> int:
    """Drop WS presence-set members whose heartbeat key has expired (ASYNC-7).

    A connection that dies without a clean disconnect leaves its user in the
    room presence SETs even though the 60 s heartbeat key is long gone. This
    reconciles the SETs so room rosters do not accumulate ghosts. The Redis walk
    lives in ``shared_kernel.realtime.presence`` so the key layout stays in one
    place.
    """
    from contexts.conversation.infrastructure.presence import scrub_stale_presence

    removed = await scrub_stale_presence()
    await _emit_summary(session, "retention.presence.scrubbed", removed)
    return removed


async def _purge_read_notifications(session: AsyncSession) -> int:
    """Hard-delete read notifications older than 90 days."""
    cutoff = now() - timedelta(days=90)
    result = await session.execute(
        sa.text(
            "DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at < :cutoff "
            "AND id IN ("
            "  SELECT id FROM notifications "
            "  WHERE read_at IS NOT NULL AND created_at < :cutoff LIMIT 1000"
            ")"
        ).bindparams(cutoff=cutoff)
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.notifications.swept", count)
    return count


_POLICIES = [
    ("messages", _purge_messages),
    ("message_attachments", _purge_message_attachments),
    ("notifications", _purge_read_notifications),
    ("audit_logs", _purge_audit_logs),
    ("workflow_runs", _archive_workflow_runs),
    ("key_usage_events", _rollup_key_usage_events),
    # Partition lifecycle must run *after* the rollup so we don't drop a
    # partition the same night its data is rolled up.
    ("key_usage_partitions", _manage_key_usage_partitions),
    ("soft_deleted", _purge_soft_deleted_tenancy),
    ("invites", _expire_invites),
    ("oc_transfers", _expire_oc_transfers),
    ("approvals", _expire_approvals),
    ("tokens", _purge_expired_tokens),
    ("sessions", _prune_idle_sessions),
    ("agent_instances", _purge_agent_instances),
    # Must run after agent_instances purge so any destroyed children are
    # already gone — leaving childless synthetic roots cleanly deletable.
    ("subagent_roots", _sweep_orphaned_subagent_roots),
    ("impersonations", _close_idle_impersonations),
    ("exports_bucket", _purge_exports_bucket),
    ("instructions_chains", _sweep_instructions_chains),
    ("tus_parts", _cleanup_tus_parts),
    ("presence", _scrub_stale_presence),
]


async def retention_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    """Master retention cron — runs all policies in sequence."""
    import time as _time

    sm = get_sessionmaker()
    report: dict[str, int] = {}
    failed: list[str] = []
    for name, func in _POLICIES:
        try:
            async with sm() as session, session.begin():
                count = await func(session)
            report[name] = count
            RETENTION_LAST_RUN_TIMESTAMP.labels(worker=name).set(_time.time())
            RETENTION_LAST_ROWS.labels(worker=name).set(count)
        except Exception:
            logger.bind(event=f"retention_{name}_error").exception(f"retention policy {name} failed")
            report[name] = -1
            failed.append(name)
            RETENTION_FAILURES.labels(worker=name).inc()
    total = sum(v for v in report.values() if v > 0)
    logger.bind(
        event="retention_sweep_done",
        report=report,
        succeeded=len(_POLICIES) - len(failed),
        failed_count=len(failed),
        failed_policies=failed,
    ).info(
        f"retention sweep complete — {total} rows affected, "
        f"{len(failed)}/{len(_POLICIES)} policies failed" + (f" ({', '.join(failed)})" if failed else "")
    )
    _ = ctx
    return report


__all__ = ["retention_sweep"]
