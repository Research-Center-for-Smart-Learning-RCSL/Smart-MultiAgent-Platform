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
import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

# The tenancy recovery window (R8.11/R8.12). Exported because `agent_fs_gc`'s
# reclamation sweep is only correct while it matches: that worker infers "this
# artifact's window has closed" from the *absence* of an agents row, which only
# follows if this sweep is the sole remover of such rows and never removes one
# early. Changing this here without changing it there reintroduces the bug the
# 2026-07-17-agent-fs-gc-retention-race dossier fixed.
SOFT_DELETE_RETENTION_DAYS = 60

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
    from contexts.activities.infrastructure.tables import (
        activity_submissions as activity_submissions_tbl,
    )
    from contexts.conversation.infrastructure.tables import workspaces as workspaces_tbl

    cutoff = now() - timedelta(days=SOFT_DELETE_RETENTION_DAYS)
    sub = activity_submissions_tbl
    # D-1 research retention: never purge a soft-deleted row while an
    # activity_submission reachable through the ON DELETE CASCADE chain is still
    # retained (retain_until in the future). The room and its submissions purge
    # together once every retain_until lapses. The cascade path is
    # org -> project -> workspace -> chatroom -> activity_submission, so deferring
    # only the *direct* chatroom purge is insufficient: purging a soft-deleted
    # project or org would cascade-delete the retained submissions early. Guard
    # every ancestor on the cascade path (workspaces are never purged here, so
    # they need no guard of their own).
    retained = sa.and_(sub.c.retain_until.is_not(None), sub.c.retain_until > sa.func.now())
    chatroom_retained = (
        sa.select(sa.literal(1))
        .select_from(sub)
        .where(sa.and_(retained, sub.c.chatroom_id == chatrooms_tbl.c.id))
        .exists()
    )
    project_retained = (
        sa.select(sa.literal(1))
        .select_from(
            sub.join(chatrooms_tbl, chatrooms_tbl.c.id == sub.c.chatroom_id).join(
                workspaces_tbl, workspaces_tbl.c.id == chatrooms_tbl.c.workspace_id
            )
        )
        .where(sa.and_(retained, workspaces_tbl.c.project_id == projects_tbl.c.id))
        .exists()
    )
    org_retained = (
        sa.select(sa.literal(1))
        .select_from(
            sub.join(chatrooms_tbl, chatrooms_tbl.c.id == sub.c.chatroom_id)
            .join(workspaces_tbl, workspaces_tbl.c.id == chatrooms_tbl.c.workspace_id)
            .join(projects_tbl, projects_tbl.c.id == workspaces_tbl.c.project_id)
        )
        .where(sa.and_(retained, projects_tbl.c.owner_org_id == orgs_tbl.c.id))
        .exists()
    )

    # F-24: before the Postgres cascade erases the rag_*/knowmap_* rows that carry
    # the blob keys, tear down each doomed project's source infra (both source
    # buckets + the File RAG per-project Qdrant collection), keyed on project_id
    # alone.
    #
    # F-7: teardown follows the *committed* delete, never the candidate read.
    # Selecting eligible ids is not ownership of the deletion — a restore can
    # clear `deleted_at` before the delete runs, leaving a live project whose
    # sources were already erased and cannot be rebuilt. Deleting with RETURNING
    # makes the committed row absence the authoritative, irreversible decision,
    # and only the returned ids may be torn down. The whole DB phase runs in its
    # own short transaction so no MinIO/Qdrant latency is ever spent holding a
    # lock on a tenancy row (the policy session must not stay open across those
    # network calls).
    org_conds = sa.and_(
        orgs_tbl.c.deleted_at.is_not(None),
        orgs_tbl.c.deleted_at < cutoff,
        ~org_retained,
    )
    sm = get_sessionmaker()
    committed_project_ids: set[uuid.UUID] = set()
    total = 0
    # `session` is deliberately unused for the destructive work: it belongs to
    # `retention_sweep`'s transaction, which must not stay open across the
    # MinIO/Qdrant calls below. The parameter stays because `_POLICIES` calls
    # every policy the same way.
    async with sm() as del_session, del_session.begin():
        # Read the doomed org ids ONCE and reuse this literal list for both the
        # membership capture and the delete. Re-running the predicate would be a
        # second READ COMMITTED snapshot: an org that became eligible in between
        # would be deleted without appearing in the mapping, and its
        # cascade-deleted projects would never be torn down. (An org *restored*
        # in between is still safe — the delete re-checks `conds` and simply
        # returns fewer rows than were mapped.)
        org_ids = list(
            (
                await del_session.execute(
                    sa.select(orgs_tbl.c.id).where(org_conds).order_by(orgs_tbl.c.id).limit(200)
                )
            )
            .scalars()
            .all()
        )
        # Capture org -> project membership BEFORE the org delete: an org-owned
        # project vanishes by FK cascade, so after the delete there is no row
        # left to attribute to the returned org id.
        org_projects: dict[uuid.UUID, set[uuid.UUID]] = {}
        if org_ids:
            rows = await del_session.execute(
                sa.select(projects_tbl.c.id, projects_tbl.c.owner_org_id).where(
                    projects_tbl.c.owner_org_id.in_(org_ids)
                )
            )
            for pid, oid in rows.all():
                org_projects.setdefault(oid, set()).add(pid)

        for tbl in _SOFT_DELETE_TABLES:
            conds = [tbl.c.deleted_at.is_not(None), tbl.c.deleted_at < cutoff]
            if tbl is chatrooms_tbl:
                conds.append(~chatroom_retained)
            elif tbl is projects_tbl:
                conds.append(~project_retained)
            elif tbl is orgs_tbl:
                conds.append(~org_retained)
            if tbl is orgs_tbl:
                # The same ids the mapping was built from, not a re-evaluation.
                stmt = sa.delete(tbl).where(sa.and_(*conds, tbl.c.id.in_(org_ids)))
            else:
                # A subquery is safe here: one statement, one snapshot.
                batch = sa.select(tbl.c.id).where(sa.and_(*conds)).order_by(tbl.c.id).limit(200)
                stmt = sa.delete(tbl).where(sa.and_(*conds, tbl.c.id.in_(batch)))
            # Only the two tables that own external data need their ids back.
            if tbl is orgs_tbl or tbl is projects_tbl:
                deleted = (await del_session.execute(stmt.returning(tbl.c.id))).scalars().all()
                total += len(deleted)
                if tbl is orgs_tbl:
                    for oid in deleted:
                        committed_project_ids |= org_projects.get(oid, set())
                else:
                    committed_project_ids.update(deleted)
            else:
                result = await del_session.execute(stmt)
                total += result.rowcount or 0
        # Same transaction as the deletes it counts, per `audit.emit`'s contract:
        # the trail must never omit an erasure the database kept.
        await _emit_summary(del_session, "retention.soft_deleted.swept", total)
    # `emit` queues its realtime tail event on the session that wrote the row, and
    # `retention_sweep` only flushes the policy session — publish this one's here
    # or the audit stream silently loses every event this policy emitted. Safe
    # after close: the flush reads `session.info` and talks to Redis, not the DB.
    await audit.flush_tail_events(del_session)

    # Past this point the deletes are committed and no restore can revive them.
    if committed_project_ids:
        await _teardown_committed_projects(sm, committed_project_ids)
    return total


async def _teardown_committed_projects(
    sm: async_sessionmaker[AsyncSession], project_ids: set[uuid.UUID]
) -> int:
    """Erase source infra for projects whose hard delete already committed (F-7).

    Runs in its own transaction, opened after the delete committed, so the
    tenancy rows are never locked while MinIO/Qdrant calls are in flight.
    Failure is safe rather than silent: the committed row absence is the durable
    retry signal, so anything missed here stays discoverable by
    ``_purge_rag_source_orphans``, whose live set is every ``projects`` row.
    Returns the count purged without error.
    """
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    purged = 0
    td_session = None
    try:
        async with sm() as td_session, td_session.begin():
            purged = await KnowledgeFacade(td_session).purge_project_source_infra_batch(
                project_ids, source="retention"
            )
            # In the same transaction as the per-project purge audits it summarises.
            await audit.emit(
                td_session,
                audit.AuditEvent(
                    action="retention.rag_source_infra.torn_down",
                    metadata={
                        "projects_committed": len(project_ids),
                        "projects_purged": purged,
                        # Non-zero means the backstop sweep still owes this pass.
                        "projects_owed": len(project_ids) - purged,
                    },
                ),
            )
    except Exception:
        # The batch isolates per-project failures internally (and reports the
        # partial verdict itself); this only covers a catastrophic failure, e.g.
        # Qdrant client construction. `purged` is already set when the external
        # work succeeded and only the commit failed — report what was actually
        # destroyed rather than claiming it is all still owed, because those
        # blobs are gone whether or not the audit row survived.
        logger.bind(
            event="retention_rag_source_teardown_failed",
            projects_committed=len(project_ids),
            projects_purged=purged,
        ).opt(exception=True).warning(
            "rag source teardown batch failed; orphan sweep will reclaim the remainder"
        )
    if td_session is not None:
        # Same reason as the delete session: nobody else flushes this one's queue.
        await audit.flush_tail_events(td_session)
    return purged


_TEARDOWN_RETRY_BATCH = 50


async def _retry_pending_collection_teardowns(session: AsyncSession) -> int:
    """Retry configless collection teardowns left owed by a failed drop (F-3).

    Unlike ``_purge_rag_source_orphans``, this is *not* keyed on a missing project
    row: the projects here are alive and well, and only their last knowledge config
    was deleted while Qdrant was unreachable, so the collection survived and its pin
    was retained to keep the dimension invariant failing closed. That state is
    invisible to the row-absence backstop, which is why it needs its own policy.

    The create path retries the same teardown on demand, so this exists to reclaim
    collections for projects nobody happens to reconfigure. Idempotent — a pin whose
    collection is already gone is released on the first pass that reaches Qdrant.

    Each pin gets its OWN transaction rather than riding this policy's session. The
    teardown's ``(project, kind)`` advisory lock is transaction-scoped, so batching
    them into the policy transaction would hold every lock until the whole pass ended
    — and in the very outage this sweep exists for, each pin burns up to
    ``qdrant.teardown_timeout_s`` before releasing. That is a sweep that blocks config
    creation across every affected project for minutes. Bounded by
    ``_TEARDOWN_RETRY_BATCH`` per pass; a larger backlog drains over subsequent nights.
    """
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    owed = await KnowledgeFacade(session).list_pending_collection_teardowns(limit=_TEARDOWN_RETRY_BATCH)
    sm = get_sessionmaker()
    released = 0
    for project_id, kind in owed:
        try:
            async with sm() as pin_session, pin_session.begin():
                outcome = await KnowledgeFacade(pin_session).retry_collection_teardown(project_id, kind)
        except Exception:
            logger.bind(event="retention_collection_teardown_failed").opt(exception=True).warning(
                "collection teardown retry failed for project %s kind %s", project_id, kind.value
            )
            continue
        if outcome.pin_released:
            released += 1
    if len(owed) == _TEARDOWN_RETRY_BATCH:
        logger.bind(event="retention_collection_teardown_capped").info(
            "teardown retry hit its per-pass cap; the remainder drains next pass"
        )
    await _emit_summary(session, "retention.collection_teardowns.released", released)
    return released


async def _purge_rag_source_orphans(session: AsyncSession) -> int:
    """Backstop sweep for File RAG + Knowledge Map source infra (F-24, mirrors F-8).

    The proactive teardown in ``_purge_soft_deleted_tenancy`` and the admin GDPR
    purge are primary; this reclaims orphans from any path — a failed teardown, a
    crash between steps, and data already leaked before this fix. The live set is
    *every* ``projects`` id (regardless of ``deleted_at``, Q-4), so a soft-deleted
    but not-yet-hard-deleted project keeps its data until hard-delete. Orphan
    discovery reads the external stores directly (via the KnowledgeFacade), so
    each orphan purge and per-store enumeration is isolated — one failure never
    aborts the cycle.
    """
    from contexts.knowledge.interfaces.facade import KnowledgeFacade

    live_ids = set((await session.execute(sa.select(projects_tbl.c.id))).scalars().all())
    swept = await KnowledgeFacade(session).sweep_rag_source_orphans(live_ids)
    await _emit_summary(session, "retention.rag_source_orphans.swept", swept)
    return swept


async def _expire_invites(session: AsyncSession) -> int:
    result = await session.execute(
        sa.text("UPDATE invites SET state = 'expired' " "WHERE state = 'pending' AND expires_at < now()")
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
    count = result.rowcount or 0
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
    count = result.rowcount or 0
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
    lives in ``contexts.conversation.infrastructure.presence`` so the key
    layout stays in one place.

    B1: a room this sweep leaves empty bypassed `PresenceTracker.leave`
    entirely, so the presence-changed/silence-pause hook a clean last-leave
    triggers (`app/api/ws/chatroom.py`'s `_notify_presence`) never ran for it.
    Drive the same hook here for each emptied room so a self-opening silence
    agent doesn't fire into a room whose last member dropped uncleanly.
    """
    from contexts.conversation.application.triggers import evaluate_presence_change
    from contexts.conversation.infrastructure.presence import scrub_stale_presence

    removed, emptied_rooms = await scrub_stale_presence()
    for room_id in emptied_rooms:
        try:
            await evaluate_presence_change(session, chatroom_id=room_id, has_live_users=False)
        except Exception:  # best-effort per room, mirrors _notify_presence
            logger.bind(event="retention_presence_notify_failed", room_id=str(room_id)).opt(
                exception=True
            ).warning("presence-changed dispatch failed for stale-scrubbed room")
    await _emit_summary(session, "retention.presence.scrubbed", removed)
    return removed


async def _purge_read_notifications(session: AsyncSession) -> int:
    """Hard-delete read notifications older than 90 days via NotificationFacade."""
    from contexts.notification.interfaces.facade import NotificationFacade

    count = await NotificationFacade(session).purge_old_read(retention_days=90)
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
    # Backstop for source infra whose project row is already gone — placed after
    # the proactive teardown so a same-run teardown miss is reclaimed immediately.
    ("rag_source_orphans", _purge_rag_source_orphans),
    # Backstop for collections whose project row is still live but whose teardown
    # could not reach Qdrant (F-3). Placed after rag_source_orphans: that sweep
    # erases collections for dead projects, so anything still pinned here belongs
    # to a live one and is genuinely a retry.
    ("collection_teardowns", _retry_pending_collection_teardowns),
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
            from shared_kernel.audit import flush_tail_events

            await flush_tail_events(session)
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
