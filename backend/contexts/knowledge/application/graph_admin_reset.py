"""Admin reset of a stuck graph config, shared by Concept Map and Knowledge Map.

R11a.02's reset is identical for both graph products -- they share the build lock, the
Redis snapshot/pointer keys, the Neo4j node space, and the 2PC state machine -- so the
logic lives here once and each config service supplies its own repository, audit action,
resource type, and error class. This mirrors :class:`ReconciliationLoop`, which is
parameterised the same way rather than duplicated per product.

Knowledge Map had no reset at all before
docs/tasks/2026-07-17-graphrag-reset-expired-recovery/: `recovery_unavailable` is outside
the reconciler's sweep set, so without a reset the only escape from it would have been a
manual rebuild, and a Knowledge Map rebuild routes through the same `{IDLE, FAILED,
RECOVERY_UNAVAILABLE}` engine gate. Extracting rather than copying is what makes the two
products' escape hatches provably identical.
"""

from __future__ import annotations

import enum
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_events import publish_build_state
from contexts.knowledge.domain.errors import (
    GraphRagBuildBusy,
    KnowledgeError,
)
from contexts.knowledge.domain.graphrag import IN_FLIGHT_BUILD_STATES, BuildState
from shared_kernel import audit

if TYPE_CHECKING:
    from contexts.knowledge.application.graphrag_ports import (
        BuildLockStore,
        GraphRagConfigRepositoryPort,
        Neo4jDriver,
        SnapshotStore,
    )

_log = logging.getLogger(__name__)

# Mirrors R11a.01's 10-minute build-lock TTL. Defined locally rather than imported
# from the reconciler because the reconciler imports this context's services --
# importing back would cycle (F-26).
BUILD_LOCK_TTL_S = 10 * 60


@dataclass(frozen=True, slots=True)
class GraphResetBinding:
    """Everything that differs between the two graph products' resets.

    Bundled rather than passed as four loose parameters so "which product" is one
    named thing: a third graph product becomes one more binding constant, not four
    more arguments threaded through the signature. Mirrors
    :class:`~contexts.knowledge.application.graphrag_reconciler.GraphConsumer`,
    which bundles the same axis for the reconciler.
    """

    configs: GraphRagConfigRepositoryPort
    audit_action: str
    resource_type: str
    compensation_error: type[KnowledgeError]
    # Per-product websocket channel, same contract as the builder's and the
    # reconciler's ``channel_fn``. No default: naming the wrong channel would
    # publish a Concept Map's state onto a Knowledge Map's socket.
    channel_fn: Callable[[uuid.UUID], str]


class DiscardPlan(enum.Enum):
    """What an admin reset may safely do about a config's in-flight build."""

    NOOP = "noop"
    COMPENSATE = "discarded"
    UNAVAILABLE = "compensation_unavailable"


def plan_discard(
    *,
    prev: BuildState,
    build_id: uuid.UUID | None,
    snapshot: dict[str, Any] | None,
) -> DiscardPlan:
    """Classify a reset's compensation options from the recovery material on hand.

    Rolling an in-flight build back needs BOTH the current-build pointer and its
    snapshot. ``delete_by_build`` on its own is not a rollback: it strips the
    build's own nodes with nothing to restore the pre-build subgraph from, so a
    config that had relations overwritten by the build loses them permanently.
    The pointer and the snapshot are two independently expiring Redis keys and
    the pointer is written last (``graphrag_builder.py``), so "pointer present,
    snapshot gone" is the ordinary shape of a lapsed 24-hour recovery window
    rather than a corruption -- and it must fail closed, not report a discard.

    A prior state outside :data:`IN_FLIGHT_BUILD_STATES` owes no compensation,
    so it keeps the historical behaviour (see FU-5 in the task dossier for the
    stale-pointer-on-a-settled-config gap this deliberately does not close).
    """
    if prev not in IN_FLIGHT_BUILD_STATES:
        return DiscardPlan.NOOP if build_id is None else DiscardPlan.COMPENSATE
    if build_id is None or snapshot is None:
        return DiscardPlan.UNAVAILABLE
    return DiscardPlan.COMPENSATE


def build_compensation_neo4j_driver() -> Neo4jDriver | None:
    """Short-lived Neo4j driver for admin_reset compensation (F-26).

    Mirrors ``cascade_external_stores``' construction; ``None`` when Neo4j is
    unconfigured, in which case an in-flight build the reset cannot roll back is
    treated as a compensation failure.
    """
    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

    neo4j_conf = getattr(get_settings(), "neo4j", None)
    if neo4j_conf is None:
        return None
    return Neo4jAsyncDriver(uri=neo4j_conf.url, auth=(neo4j_conf.user, neo4j_conf.password))


async def _emit_reset_audit(
    db: AsyncSession,
    *,
    config_id: uuid.UUID,
    project_id: uuid.UUID,
    prev: BuildState,
    build_id: uuid.UUID | None,
    forced: bool,
    outcome: str,
    actor_user_id: uuid.UUID,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
    audit_action: str,
    resource_type: str,
) -> None:
    """Identifiers only. Never snapshot contents, graph facts, or error text."""
    await audit.emit(
        db,
        audit.AuditEvent(
            action=audit_action,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            resource_type=resource_type,
            resource_id=config_id,
            metadata={
                "previous_state": prev.value,
                "project_id": str(project_id),
                "build_id": str(build_id) if build_id is not None else None,
                "forced": forced,
                "outcome": outcome,
            },
            request_id=request_id,
        ),
    )


async def perform_admin_reset(
    db: AsyncSession,
    *,
    config_id: uuid.UUID,
    binding: GraphResetBinding,
    snapshots: SnapshotStore,
    locks: BuildLockStore,
    neo4j: Neo4jDriver | None,
    actor_user_id: uuid.UUID,
    actor_ip: str | None,
    force: bool,
    request_id: uuid.UUID | None,
) -> None:
    """R11a.02 -- reset a stuck config to ``idle`` after compensating 2PC state (F-26).

    Acquires the build lock (serialising against the builder + reconciler, R11a.01),
    discards any in-flight build via the same infrastructure store ports the
    reconciler's rollback uses -- Neo4j rolled back to the pre-build snapshot, snapshot
    deleted, current pointer cleared -- then forces ``idle``.

    ``force=false`` (default): a held lock raises ``GraphRagBuildBusy`` (409); a
    compensation that fails *or was never possible* raises ``binding.compensation_error``
    without forcing idle or destroying recovery material, so the config never advertises
    inconsistent state as healthy (R11.04). "Never possible" is :func:`plan_discard`'s
    ``UNAVAILABLE`` -- an in-flight build whose pointer or snapshot has expired past the
    24-hour window; that path touches no store at all.

    ``force=true`` overrides a held lock (force-release + re-acquire) and clears the
    stuck state, recording the incomplete outcome (non-null ``last_build_error``, audit
    ``outcome=compensation_failed`` or ``compensation_unavailable``). It lands on
    ``idle`` **except** on the ``UNAVAILABLE`` path, which lands on
    ``recovery_unavailable``: forcing ``idle`` there would re-open reads on a partially
    applied build that can never be rolled back, and it buys nothing, because
    ``recovery_unavailable`` is already rebuildable. The reconciler is not touched.

    The state driving all of this is re-read **after** the lock is held, not taken from
    the caller's pre-lock fetch -- serialising against the builder is the whole point of
    the lock, and a stale state would let a crashed build be misclassified as a settled
    one. Every reset is audit-logged and publishes the new state. The caller re-reads the
    config afterwards.
    """
    # Build the compensation Neo4j driver lazily, only when an in-flight build must
    # actually be rolled back -- so the 409 fast-fail and clean/idle resets construct
    # nothing. ``owns_neo4j`` stays False for an injected driver.
    owns_neo4j = False

    try:
        # --- serialize against builder / reconciler (R11a.01) ---
        if not await locks.acquire(config_id, ttl_s=BUILD_LOCK_TTL_S):
            if not force:
                raise GraphRagBuildBusy(str(config_id))
            # Admin-accepted override: break the lock and re-take it under this
            # process's token so the trailing release is a clean, owned release.
            await locks.force_release(config_id)
            if not await locks.acquire(config_id, ttl_s=BUILD_LOCK_TTL_S):
                # A concurrent worker re-grabbed the lock in the force-release window.
                # force=true means the admin accepted interrupting a live build, so
                # proceed -- but record that compensation ran without holding the lock
                # (the trailing token-checked release no-ops).
                _log.warning(
                    "admin reset: could not re-acquire lock after force-release for "
                    "config %s; compensating without the lock (force=true)",
                    config_id,
                )
        try:
            # Re-read under the lock. The caller fetched the config first (to raise its
            # own not-found before taking a lock), but that read races the builder: a
            # build can start, crash, and leave a current-build pointer between the two.
            # Deciding on the stale state would classify that crashed build as a settled
            # config and take the delete-without-restore path this function exists to
            # refuse.
            cfg = await binding.configs.get(config_id)
            if cfg is None:
                # Deleted between the caller's read and the lock. Nothing to compensate;
                # the caller's trailing re-read raises its own not-found.
                return
            prev = cfg.last_build_state
            project_id = cfg.project_id

            build_id = await snapshots.get_current(config_id=config_id)
            snapshot = (
                await snapshots.get(config_id=config_id, build_id=build_id) if build_id is not None else None
            )
            plan = plan_discard(prev=prev, build_id=build_id, snapshot=snapshot)
            comp_error: str | None = None
            outcome = plan.value

            if plan is DiscardPlan.UNAVAILABLE:
                # The 24h recovery window lapsed (or the material was evicted). Touch
                # nothing: no delete-only rollback, no pointer/snapshot clear. Whatever
                # material survives is the only thing a later forced reset or an
                # operator could work from.
                _log.warning(
                    "admin reset: recovery material unavailable for config %s (state=%s, build_id=%s)",
                    config_id,
                    prev.value,
                    build_id,
                )
                comp_error = "admin reset: recovery material unavailable"
            elif plan is DiscardPlan.COMPENSATE:
                assert build_id is not None  # narrowed by plan_discard
                if neo4j is None:
                    neo4j = build_compensation_neo4j_driver()
                    owns_neo4j = neo4j is not None
                try:
                    if neo4j is None:
                        # Cannot roll back an in-flight build with no driver -- treat as
                        # a compensation failure (the stores-down case).
                        raise RuntimeError("neo4j unavailable for compensation")
                    await neo4j.delete_by_build(config_id=config_id, build_id=build_id)
                    if snapshot is not None:
                        await neo4j.restore_from_snapshot(config_id=config_id, snapshot=snapshot)
                except (TypeError, AttributeError, NameError, ImportError):
                    # Programming errors, not store failures. Folding them into
                    # comp_error would surface a code defect as a 503 the error class
                    # documents as transient -- an operator would retry it forever.
                    raise
                except Exception:
                    _log.exception("admin reset: neo4j compensation failed for config %s", config_id)
                    comp_error = "admin reset: compensation incomplete"
                    outcome = "compensation_failed"
                if comp_error is None:
                    # Only after a successful Neo4j rollback drop the recovery material
                    # -- Neo4j first, Redis-delete on success -- so a partially
                    # compensated graph is never stranded without its snapshot.
                    await snapshots.delete(config_id=config_id, build_id=build_id)
                    await snapshots.clear_current(config_id=config_id)
            else:
                # No in-flight build -- clear any stale pointer; idempotent.
                await snapshots.clear_current(config_id=config_id)

            if comp_error is not None and not force:
                # Refuse (R11.04 / R11a.02): keep the recovery material, leave the config
                # in its prior state, do NOT set idle. Persist the audit before the 5xx
                # unwinds the request transaction (db_session rolls back on exception),
                # so the outcome is durable. ``outcome`` distinguishes a compensation
                # that failed (retryable) from one that was never possible (terminal).
                await _emit_reset_audit(
                    db,
                    config_id=config_id,
                    project_id=project_id,
                    prev=prev,
                    build_id=build_id,
                    forced=False,
                    outcome=outcome,
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                    audit_action=binding.audit_action,
                    resource_type=binding.resource_type,
                )
                await db.commit()
                raise binding.compensation_error(str(config_id))

            # Clean discard, no-op, or force=true override. ``comp_error`` is non-null
            # only on a force=true failed/unavailable compensation -- recorded as a
            # non-null last_build_error so the state is honest.
            #
            # A forced UNAVAILABLE lands on RECOVERY_UNAVAILABLE rather than IDLE: the
            # graph holds a partially applied build no rollback can undo, so advertising
            # it as readable would re-expose exactly what that state exists to hide.
            # Forcing IDLE would not help either way -- RECOVERY_UNAVAILABLE is already
            # accepted by the manual build endpoint and the builder engine.
            new_state = (
                BuildState.RECOVERY_UNAVAILABLE if plan is DiscardPlan.UNAVAILABLE else BuildState.IDLE
            )
            await binding.configs.set_state(
                config_id=config_id,
                state=new_state,
                error=comp_error,
            )
            # Terminal state change: tell live subscribers, or a list view keeps the
            # pre-reset badge until its next refetch (useBuildStateSocket stops polling
            # a config once it believes the state is terminal).
            await publish_build_state(
                config_id,
                new_state.value,
                build_id=build_id,
                channel=binding.channel_fn(config_id),
            )
            await _emit_reset_audit(
                db,
                config_id=config_id,
                project_id=project_id,
                prev=prev,
                build_id=build_id,
                forced=force,
                outcome=outcome,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
                audit_action=binding.audit_action,
                resource_type=binding.resource_type,
            )
        finally:
            await locks.release(config_id)
    finally:
        # Close only the driver this call constructed (never an injected one).
        # ``close`` is on the concrete Neo4jAsyncDriver, not the port Protocol.
        if owns_neo4j and neo4j is not None:
            closer = getattr(neo4j, "close", None)
            if closer is not None:
                await closer()
