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
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.message_attachments.swept", count)
    return count


async def _purge_audit_logs(session: AsyncSession) -> int:
    cutoff = now() - timedelta(days=365)
    # Bypass the audit_logs append-only trigger by running as the retention
    # role. SET ROLE requires membership, granted to the app/worker role by
    # migration 0027_audit_retention_grant (DB-4).
    await session.execute(sa.text("SET ROLE smap_audit_retention"))
    try:
        result = await session.execute(
            sa.text(
                "DELETE FROM audit_logs WHERE created_at < :cutoff "
                "AND id IN (SELECT id FROM audit_logs WHERE created_at < :cutoff LIMIT 1000)"
            ).bindparams(cutoff=cutoff)
        )
        count = result.rowcount or 0  # type: ignore[attr-defined]
    finally:
        await session.execute(sa.text("RESET ROLE"))
    await _emit_summary(session, "retention.audit_logs.swept", count)
    return count


async def _archive_workflow_runs(session: AsyncSession) -> int:
    """Archive workflow runs ended > 90 days ago into ``workflow_runs_archive``.

    Writes the full archive row — ``trigger_type``, ``started_by_user_id`` and
    a node-count / failure ``summary`` — and is idempotent: ``ON CONFLICT DO
    NOTHING`` plus the ``NOT IN`` guard mean a re-run, or an overlap with a
    previous partially-completed batch, can never raise a primary-key
    violation on ``workflow_runs_archive``.
    """
    cutoff = now() - timedelta(days=90)
    result = await session.execute(
        sa.text(
            "INSERT INTO workflow_runs_archive "
            "(id, workflow_id, trigger_type, started_by_user_id, state, "
            " started_at, ended_at, summary) "
            "SELECT wr.id, wr.workflow_id, wr.trigger_type, wr.started_by_user_id, "
            "       wr.state, wr.started_at, wr.ended_at, "
            "       jsonb_build_object("
            "         'node_count', "
            "         (SELECT count(*) FROM workflow_steps ws WHERE ws.run_id = wr.id), "
            "         'failures', "
            "         (SELECT count(*) FROM workflow_steps ws "
            "          WHERE ws.run_id = wr.id AND ws.state = 'failed')"
            "       ) "
            "FROM workflow_runs wr "
            "WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff "
            "  AND wr.id NOT IN (SELECT id FROM workflow_runs_archive) "
            "LIMIT 500 "
            "ON CONFLICT (id) DO NOTHING"
        ).bindparams(cutoff=cutoff)
    )
    archived = result.rowcount or 0  # type: ignore[attr-defined]
    if archived > 0:
        # Delete steps and source rows only for the runs that were just
        # archived (joined against workflow_runs_archive), not a fresh LIMIT
        # subquery that could select different rows than the archive batch.
        await session.execute(
            sa.text(
                "DELETE FROM workflow_steps WHERE run_id IN ("
                "  SELECT wr.id FROM workflow_runs wr"
                "  JOIN workflow_runs_archive wra ON wra.id = wr.id"
                "  WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff"
                ")"
            ).bindparams(cutoff=cutoff)
        )
        await session.execute(
            sa.text(
                "DELETE FROM workflow_runs WHERE id IN ("
                "  SELECT wr.id FROM workflow_runs wr"
                "  JOIN workflow_runs_archive wra ON wra.id = wr.id"
                "  WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff"
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
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
    # The synthetic-root rows are isolated in a CTE first so the ``::uuid``
    # cast only ever runs on a value our own code wrote (always a valid uuid),
    # never on some other run_context shape. The NOT EXISTS then probes the
    # workflow_runs primary key directly.
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
    """Delete MinIO export objects older than 24 h (§21.5).

    Per-object delete is retried with exponential backoff (3 attempts, base 0.5 s)
    so a transient network blip doesn't strand objects until the next sweep.
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
    count = result.rowcount or 0  # type: ignore[attr-defined]
    await _emit_summary(session, "retention.instructions_chains.swept", count)
    return count


async def _manage_key_usage_partitions(session: AsyncSession) -> int:
    """Monthly-range partition manager for ``key_usage_events`` (B3).

    Per ``alembic/versions/0008_key_usage_events.py``, the ``at`` column is the
    range key. This worker:

    1. Creates the partition for the *next* calendar month if absent.
    2. Detaches and drops partitions whose upper bound is older than 13 months
       (data already rolled up into ``key_usage_daily``).

    A single ``DEFAULT`` partition is allowed to remain as a safety net for
    rows whose ``at`` falls outside any explicit range.

    Returned int = number of partitions created + dropped (for the sweep
    summary; failures still raise to be counted by RETENTION_FAILURES).
    """
    today = now().date()
    # Anchor on UTC month boundaries.
    year = today.year
    month = today.month
    # next month
    nm_year, nm_month = (year + 1, 1) if month == 12 else (year, month + 1)
    nm_after_year, nm_after_month = (nm_year + 1, 1) if nm_month == 12 else (nm_year, nm_month + 1)
    next_part_name = f"key_usage_events_{nm_year:04d}{nm_month:02d}"
    next_lower = f"{nm_year:04d}-{nm_month:02d}-01"
    next_upper = f"{nm_after_year:04d}-{nm_after_month:02d}-01"

    created = 0
    dropped = 0

    # 1. Create next month's partition if missing. CREATE TABLE IF NOT EXISTS
    #    PARTITION OF is supported in PostgreSQL 11+ and is idempotent.
    create_sql = (
        f'CREATE TABLE IF NOT EXISTS "{next_part_name}" '
        f"PARTITION OF key_usage_events "
        f"FOR VALUES FROM ('{next_lower}') TO ('{next_upper}')"
    )
    before = await session.execute(
        sa.text("SELECT 1 FROM pg_class WHERE relname = :rn").bindparams(rn=next_part_name)
    )
    existed = before.scalar() is not None
    await session.execute(sa.text(create_sql))
    if not existed:
        created += 1

    # 2. Detach + drop partitions older than 13 months. We list partitions and
    #    parse their upper-bound expression from pg_get_expr. Only drop named
    #    partitions matching key_usage_events_YYYYMM; leave _default alone.
    cutoff = today - timedelta(days=13 * 30)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    rows = await session.execute(
        sa.text(
            "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound "
            "FROM pg_inherits i "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE p.relname = 'key_usage_events' "
            "  AND c.relname ~ '^key_usage_events_[0-9]{6}$'"
        )
    )
    for relname, bound in rows.fetchall():
        # bound looks like: FOR VALUES FROM ('2025-01-01') TO ('2025-02-01')
        if bound is None:
            continue
        # extract the upper bound date
        try:
            after_to = bound.split(" TO ", 1)[1]
            upper = after_to.split("'", 2)[1]
        except (IndexError, ValueError):
            continue
        if upper <= cutoff_str:
            await session.execute(sa.text(f'ALTER TABLE key_usage_events DETACH PARTITION "{relname}"'))
            await session.execute(sa.text(f'DROP TABLE "{relname}"'))
            dropped += 1

    total = created + dropped
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
    from shared_kernel.realtime.presence import scrub_stale_presence

    removed = await scrub_stale_presence()
    await _emit_summary(session, "retention.presence.scrubbed", removed)
    return removed


_POLICIES = [
    ("messages", _purge_messages),
    ("message_attachments", _purge_message_attachments),
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
