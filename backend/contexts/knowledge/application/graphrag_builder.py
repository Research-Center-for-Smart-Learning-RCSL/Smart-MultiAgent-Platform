"""GraphRAG build orchestrator — 2PC state machine (E.7 / §11.2a / R11.04).

State transitions:

    idle → running → neo4j_committed → qdrant_committed → idle

Phase-1 failure (triple extraction or Neo4j write) → state ``failed``;
nothing was committed anywhere so there is nothing to compensate.

Phase-2 failure (Qdrant upsert after Neo4j commit) → state
``failed_compensating``. The reconciliation loop
(:mod:`graphrag_reconciler`) retries the Qdrant phase up to 5× with
exponential backoff; if that is exhausted it rolls Neo4j back from the
Redis snapshot and finalises the state as ``failed``.

A Redis build lock (R11a.01, 10-min TTL) serialises runs per config, and
a Redis snapshot of the prior subgraph is taken before Phase-1 so the
reconciler has something to restore.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_config_service import (
    resolve_live_builder_group_project_id,
)
from contexts.knowledge.application.graphrag_events import publish_build_state
from contexts.knowledge.application.graphrag_ports import (
    BuildLockStore,
    ConfigLike,
    DeltaMessage,
    GraphRagConfigRepositoryPort,
    Neo4jDriver,
    SnapshotStore,
    TripleExtractor,
)
from contexts.knowledge.domain.errors import (
    GraphRagBuildBusy,
    GraphRagBuilderKeyGroupProjectMismatch,
    GraphRagBuildFailed,
)
from contexts.knowledge.domain.graphrag import (
    BuildResult,
    BuildState,
    Triple,
    deterministic_point_id,
)
from contexts.knowledge.infrastructure.graphrag_vector_store import (
    GraphRagVectorStore,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now

_log = logging.getLogger(__name__)

LOCK_TTL_S = 10 * 60  # R11a.01
SNAPSHOT_TTL_S = 24 * 60 * 60  # 24h — reconciler runs at 60s period

# Audit M7: cap how many relation fragments contribute to one entity's
# embedding description. A hot entity that appears in hundreds of triples would
# otherwise build a description that exceeds the embedder's input-token limit
# and fail Phase-2 on every reconciler retry. Bounding it keeps the description
# representative without unbounded growth.
MAX_DESC_FRAGMENTS = 40


def attach_temporal_provenance(triple: Triple, msg_created_at: dict[str, float]) -> Triple:
    """Stamp a triple's first/last-seen from its evidence messages (WS5 R11.21).

    ``msg_created_at`` maps message id -> the message's ``created_at`` as UTC epoch
    seconds, built from the delta feed. A relation's ``first_seen_at`` is the
    earliest and ``last_seen_at`` the latest ``created_at`` among its evidence
    messages — derived only from message timestamps, never from LLM output.
    Returns the triple unchanged when no evidence resolves to a timestamp (a
    relation the extractor left evidence-less). Neo4j MERGE then keeps first-seen
    earliest / last-seen latest across delta builds and restatements.
    """
    stamps = [msg_created_at[ref] for ref in triple.evidence_refs if ref in msg_created_at]
    if not stamps:
        return triple
    return replace(triple, first_seen_at=min(stamps), last_seen_at=max(stamps))


def attach_member_provenance(triple: Triple, msg_member: dict[str, str]) -> Triple:
    """Tag a triple with the member(s) whose messages produced it (R11.22).

    ``msg_member`` maps message id -> source member agent id (both strings),
    built from the delta feed. A relation's provenance is the set of members
    behind its evidence messages: a relation two members independently stated
    carries both, so a member-scoped retrieval filter returns each member's true
    contributions. Derived only from message provenance — never from LLM output.
    Returns the triple unchanged when no evidence resolves to a member (a
    single-owner build, or a relation the extractor left evidence-less).
    """
    members = sorted({msg_member[ref] for ref in triple.evidence_refs if ref in msg_member})
    if not members:
        return triple
    return replace(triple, source_member_ids=tuple(members))


def build_entity_descriptions(
    triples: Iterable[tuple[str, str, str]],
) -> list[tuple[str, str]]:
    """Map (subject, relation, object) triples to sorted (entity, description) pairs.

    Audit review #8: single source of truth shared by the builder's Phase-2 and
    the reconciler's Phase-2 retry, so a recovered build re-embeds entities with
    exactly the same description (and therefore comparable vectors) as the
    original build. Each entity's description is the ``" | "``-joined relation
    fragments it participates in, capped at :data:`MAX_DESC_FRAGMENTS`.
    """
    entities: dict[str, list[str]] = {}
    for subject, relation, obj in triples:
        fragment = f"{subject} {relation} {obj}"
        entities.setdefault(subject, []).append(fragment)
        entities.setdefault(obj, []).append(fragment)
    return [(name, " | ".join(frags[:MAX_DESC_FRAGMENTS])) for name, frags in sorted(entities.items())]


@dataclass(frozen=True, slots=True)
class EntityEmbedding:
    """Interim product of the embed step — surfaced for tests + workers."""

    point_id: uuid.UUID
    entity: str
    description: str
    vector: list[float]


class GraphRagBuilder:
    """Owns the full 2PC lifecycle for a single build."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        neo4j: Neo4jDriver,
        vector_store: GraphRagVectorStore,
        extractor: TripleExtractor,
        lock_store: BuildLockStore,
        snapshot_store: SnapshotStore,
        delta_loader: DeltaLoader,
        embedder_factory: EmbedderFactory,
        configs: GraphRagConfigRepositoryPort,
        channel_fn: Callable[[uuid.UUID], str],
    ) -> None:
        self._db = db
        self._neo4j = neo4j
        self._vectors = vector_store
        self._extractor = extractor
        self._locks = lock_store
        self._snapshots = snapshot_store
        self._delta_loader = delta_loader
        self._embedder_factory = embedder_factory
        self._configs = configs
        # R11.15: the engine is shared across GraphRAG and Knowledge Map, so it
        # cannot assume which domain's channel to publish build-state to —
        # every caller must name its own (graphrag_channel / knowmap_channel).
        # No default: a silent fallback here previously let a caller that
        # forgot to wire this misdirect its events onto another product's
        # channel with no error (found in code review).
        self._channel_fn = channel_fn

    async def run(
        self,
        *,
        config_id: uuid.UUID,
        mode: Literal["delta", "full"] = "delta",
        triggered_by: str = "manual",
        replace: bool = False,
    ) -> BuildResult:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagBuildFailed(f"config {config_id} missing")
        # D10: capture the start watermark BEFORE reading the delta. last_build_at
        # is stamped with this on success, so any message created while the build
        # runs (after the delta was read) is picked up by the next build instead
        # of being skipped. Re-reading a boundary message is harmless — apply is
        # idempotent and the supersede sweep dedups by entity.
        build_started_at = now()
        if not await self._locks.acquire(config_id, ttl_s=LOCK_TTL_S):
            raise GraphRagBuildBusy(str(config_id))

        build_id = uuid.uuid4()
        try:
            return await self._run_locked(
                cfg=cfg,
                build_id=build_id,
                mode=mode,
                triggered_by=triggered_by,
                build_started_at=build_started_at,
                replace=replace,
            )
        finally:
            await self._locks.release(config_id)

    async def _run_locked(
        self,
        *,
        cfg: ConfigLike,
        build_id: uuid.UUID,
        mode: Literal["delta", "full"],
        triggered_by: str,
        build_started_at: datetime,
        replace: bool = False,
    ) -> BuildResult:
        # idle/failed/recovery_unavailable → running. Anything else is a refusal.
        #
        # RECOVERY_UNAVAILABLE is accepted here even though it is read-blocked and
        # outside the reconciler's sweep set: a fresh build is the only thing that can
        # ever make such a config readable again, since its partial Neo4j state can
        # never be rolled back. Automatic triggers deliberately do NOT admit it (see
        # _BUILDABLE_STATES in graphrag_triggers.py) — rebuilding over known-inconsistent
        # data must be a human decision, and this gate is also reached by the manual
        # endpoint and by admin reset's aftermath.
        if cfg.last_build_state not in {
            BuildState.IDLE,
            BuildState.FAILED,
            BuildState.RECOVERY_UNAVAILABLE,
        }:
            raise GraphRagBuildBusy(f"config {cfg.id} in non-resumable state {cfg.last_build_state.value}")

        # R7.09a pre-flight: re-verify the builder key group is still live and in
        # this config's project before spending anything. Validated on write
        # (graphrag_config_service.py), re-checked here at dispatch time so a group
        # deleted after the config was created fails loudly with the same
        # actionable error instead of surfacing as an empty eligible-member list
        # partway through extraction (fixed for free by group_repository.py's join,
        # but that reads as indistinguishable-from-exhaustion, not this).
        builder_group_project_id = await resolve_live_builder_group_project_id(
            self._db, cfg.builder_key_group_id
        )
        if builder_group_project_id is None or builder_group_project_id != cfg.project_id:
            error = (
                f"builder_key_group_id {cfg.builder_key_group_id} does not belong to project {cfg.project_id}"
            )
            # R7.09a: never silently -- record FAILED + an audit event before
            # raising, so this is visible via the config's own state, not only
            # in worker logs (unlike the BuildBusy refusal above, this is a
            # permanent condition, not a transient one worth leaving unrecorded).
            await self._fail_preflight(cfg.id, build_id, error)
            raise GraphRagBuilderKeyGroupProjectMismatch(error)

        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.RUNNING,
            error=None,
            # F-4: the only place that knows a build just started. last_build_at is
            # written on terminal outcomes only, so without this a RUNNING config
            # has no readable age and a stuck build cannot be told from a fresh one.
            stamp_started_at=True,
        )
        await publish_build_state(
            cfg.id, BuildState.RUNNING.value, build_id=build_id, channel=self._channel_fn(cfg.id)
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_started",
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "build_id": str(build_id),
                    "mode": mode,
                    "triggered_by": triggered_by,
                },
            ),
        )
        # Audit C1: persist each transition durably. The worker only committed
        # once after run() returned, collapsing RUNNING/NEO4J_COMMITTED into the
        # terminal state — so a crash mid-build left Postgres rolled back while
        # Neo4j kept the orphan triples, invisible to the reconciler (C2).
        await self._db.commit()

        # Snapshot the prior subgraph BEFORE we touch anything — so Phase-2
        # failure has something to roll back to. The current-build pointer is
        # the authoritative record of the in-flight build id the reconciler
        # reads (audit C4) instead of guessing from a key scan.
        try:
            prior = await self._neo4j.snapshot_subgraph(
                config_id=cfg.id,
                build_id=None,
            )
            await self._snapshots.put(
                config_id=cfg.id,
                build_id=build_id,
                snapshot=prior,
                ttl_s=SNAPSHOT_TTL_S,
            )
            await self._snapshots.set_current(
                config_id=cfg.id,
                build_id=build_id,
                ttl_s=SNAPSHOT_TTL_S,
            )
        except Exception as exc:
            await self._fail_phase1(cfg.id, build_id, f"snapshot: {exc}")
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED,
                triples_written=0,
                entities_written=0,
                error=str(exc),
            )

        # ------------ Phase 1: extract triples + upsert Neo4j -----------
        try:
            # Audit M6: a "full" rebuild must re-extract the whole history, not
            # just messages newer than the last build. Pass since=None so the
            # loader scans from the beginning.
            since = None if mode == "full" else cfg.last_build_at
            # D1: process history in bounded windows so memory and per-call LLM
            # payload stay flat regardless of history size. Extraction runs per
            # window and triples accumulate; Neo4j apply / embed / Qdrant upsert
            # all happen once for the whole build (one atomic 2PC commit).
            triples: list[Triple] = []
            # Phase 2b (R11.22): message id -> source member agent id, accumulated
            # across windows so a relation's provenance resolves even when its
            # evidence spans windows. Empty for a single-owner build, leaving
            # triples untagged (source_member_ids == ()).
            msg_member: dict[str, str] = {}
            # Phase 2b WS5 (R11.21): message id -> created_at (UTC epoch seconds),
            # accumulated across windows so a relation's first/last-seen resolves
            # even when its evidence spans windows. Always populated (every
            # message has a created_at), unlike the member map.
            msg_created_at: dict[str, float] = {}
            async for window in self._delta_loader.iter_windows(
                config_id=cfg.id,
                since=since,
                mode=mode,
            ):
                for m in window:
                    msg_created_at[str(m.id)] = m.created_at.timestamp()
                    if m.source_member_id is not None:
                        msg_member[str(m.id)] = str(m.source_member_id)
                window_triples = await self._extractor.extract(
                    config_id=cfg.id,
                    builder_key_group_id=cfg.builder_key_group_id,
                    messages=window,
                )
                triples.extend(window_triples)
                # D3: refresh the lock at every window boundary — extraction can be
                # slow, so refreshing only at commit points let the TTL lapse
                # mid-build. Fail closed if the lock was lost (another build may
                # have taken over) so we never write Neo4j concurrently.
                if not await self._locks.refresh(cfg.id, ttl_s=LOCK_TTL_S):
                    raise GraphRagBuildBusy(f"lock lost during phase-1 for {cfg.id}")
            if msg_created_at:
                triples = [attach_temporal_provenance(tr, msg_created_at) for tr in triples]
            if msg_member:
                triples = [attach_member_provenance(tr, msg_member) for tr in triples]
            # F-6: replacement upserts and prunes in one Neo4j transaction; never
            # split it back into two port calls. Rationale on
            # ``Neo4jDriver.replace_triples``.
            write_triples = self._neo4j.replace_triples if replace else self._neo4j.apply_triples
            n_triples = await write_triples(
                config_id=cfg.id,
                project_id=cfg.project_id,
                build_id=build_id,
                triples=triples,
            )
        except Exception as exc:
            await self._fail_phase1(cfg.id, build_id, str(exc))
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED,
                triples_written=0,
                entities_written=0,
                error=str(exc),
            )

        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.NEO4J_COMMITTED,
            error=None,
        )
        await publish_build_state(
            cfg.id, BuildState.NEO4J_COMMITTED.value, build_id=build_id, channel=self._channel_fn(cfg.id)
        )
        # Audit C1/C2: make NEO4J_COMMITTED durable so a crash before Phase-2
        # finishes leaves a row the reconciler can pick up and heal.
        await self._db.commit()

        # ------------ Phase 2: embed + upsert Qdrant ---------------------
        try:
            embeddings, resolved_embedder = await self._embed_entities(
                cfg=cfg,
                build_id=build_id,
                triples=triples,
            )
            if embeddings:
                # Audit review #3: embedding can be slow too — refresh + verify
                # ownership before the Qdrant write.
                if not await self._locks.refresh(cfg.id, ttl_s=LOCK_TTL_S):
                    raise GraphRagBuildFailed(f"lock lost before qdrant upsert for {cfg.id}")
                await self._vectors.ensure_graphrag_collection(
                    cfg.project_id,
                    vector_size=len(embeddings[0].vector),
                )
                await self._vectors.upsert_entities(
                    project_id=cfg.project_id,
                    config_id=cfg.id,
                    build_id=build_id,
                    points=[(e.point_id, e.vector, e.entity, e.description) for e in embeddings],
                )
        except Exception as exc:
            # Phase-2 failure: go to failed_compensating; reconciler takes it.
            await self._configs.set_state(
                config_id=cfg.id,
                state=BuildState.FAILED_COMPENSATING,
                error=f"phase2: {exc}",
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="graphrag.build_failed",
                    resource_type="graphrag_config",
                    resource_id=cfg.id,
                    metadata={
                        "build_id": str(build_id),
                        "phase": "qdrant",
                        "error": str(exc),
                    },
                ),
            )
            await publish_build_state(
                cfg.id,
                BuildState.FAILED_COMPENSATING.value,
                build_id=build_id,
                channel=self._channel_fn(cfg.id),
            )
            # Audit C1/C2: persist FAILED_COMPENSATING durably; the current-build
            # pointer is intentionally left set so the reconciler resolves this
            # build's id and retries Phase-2 (or rolls back).
            await self._db.commit()
            return BuildResult(
                config_id=cfg.id,
                build_id=build_id,
                state=BuildState.FAILED_COMPENSATING,
                triples_written=n_triples,
                entities_written=0,
                error=str(exc),
            )

        # Both phases committed → supersede stale duplicates + finalise.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.QDRANT_COMMITTED,
            error=None,
        )
        # DOM-8: the GraphRAG entity collection accumulates across delta
        # builds — each build embeds only the entities in its own delta, so
        # earlier builds' points for *untouched* entities are still live and
        # MUST be kept. Only the points this build re-embedded supersede an
        # older copy: delete prior-build points for exactly those entity
        # names. Best-effort — the build has already succeeded, so a sweep
        # failure is logged, not fatal.
        #
        # F-6: a full-corpus knowmap replacement instead removes EVERY prior-build
        # point for the config (a strict superset of the name-scoped supersede) so
        # vectors for entities the current corpus no longer produces are cleaned up.
        # Runs even when this build embedded nothing (a corpus emptied to zero
        # entities), so the last surviving vectors are purged.
        if replace:
            try:
                await self._vectors.delete_points_not_in_build(
                    project_id=cfg.project_id,
                    config_id=cfg.id,
                    keep_build_id=build_id,
                )
            except Exception as exc:  # best-effort cleanup; never fail the build
                _log.warning(
                    "graphrag build-scoped vector sweep failed for config %s build %s: %s",
                    cfg.id,
                    build_id,
                    exc,
                )
        elif embeddings:
            try:
                await self._vectors.delete_superseded_entities(
                    project_id=cfg.project_id,
                    config_id=cfg.id,
                    keep_build_id=build_id,
                    entities=[e.entity for e in embeddings],
                )
            except Exception as exc:  # best-effort cleanup; never fail the build
                _log.warning(
                    "graphrag superseded-entity sweep failed for config %s build %s: %s",
                    cfg.id,
                    build_id,
                    exc,
                )
        # D2 self-pin: a legacy null-pin config records the embedding identity it
        # just built with, so every later build selects the same provider/model
        # and the project's vector dimension stays fixed. Written in the same
        # transaction as the terminal commit below.
        if embeddings and resolved_embedder is not None and cfg.embed_dim is None:
            await self._configs.set_embed_pin(
                config_id=cfg.id,
                provider=resolved_embedder.provider,
                model=resolved_embedder.model,
                dim=len(embeddings[0].vector),
            )
        # Final idle state + stamp last_build_at with the D10 started-at watermark.
        await self._configs.set_state(
            config_id=cfg.id,
            state=BuildState.IDLE,
            error=None,
            built_at=build_started_at,
        )
        # Audit review #2: make the terminal IDLE durable BEFORE dropping the
        # Redis snapshot + current-build pointer. Otherwise a crash between the
        # cleanup and the worker's outer commit would roll Postgres back to
        # NEO4J_COMMITTED with the snapshot/pointer already gone — and the
        # reconciler would mark a genuinely-finished build FAILED.
        await self._db.commit()
        await self._snapshots.delete(config_id=cfg.id, build_id=build_id)
        await self._snapshots.clear_current(config_id=cfg.id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_finished",
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "build_id": str(build_id),
                    "triples": n_triples,
                    "entities": len(embeddings),
                },
            ),
        )
        await publish_build_state(
            cfg.id,
            BuildState.IDLE.value,
            build_id=build_id,
            channel=self._channel_fn(cfg.id),
            triples=n_triples,
            entities=len(embeddings),
        )
        return BuildResult(
            config_id=cfg.id,
            build_id=build_id,
            state=BuildState.IDLE,
            triples_written=n_triples,
            entities_written=len(embeddings),
            error=None,
        )

    async def _fail_preflight(
        self,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        error: str,
    ) -> None:
        """R7.09a: record a pre-flight rejection the same way every other build
        failure is recorded -- FAILED state, an audit event, and the build-state
        channel -- before the caller raises. No snapshot exists yet at this point
        (unlike `_fail_phase1`, which runs after Phase-1 has taken one), so there
        is nothing to delete or clear here.
        """
        await self._configs.set_state(
            config_id=config_id,
            state=BuildState.FAILED,
            error=error,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_failed",
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "build_id": str(build_id),
                    "phase": "pre-flight",
                    "error": error,
                },
            ),
        )
        await publish_build_state(
            config_id, BuildState.FAILED.value, build_id=build_id, channel=self._channel_fn(config_id)
        )
        await self._db.commit()

    async def _fail_phase1(
        self,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        error: str,
    ) -> None:
        await self._configs.set_state(
            config_id=config_id,
            state=BuildState.FAILED,
            error=error,
        )
        await self._snapshots.delete(
            config_id=config_id,
            build_id=build_id,
        )
        await self._snapshots.clear_current(config_id=config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.build_failed",
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "build_id": str(build_id),
                    "phase": "neo4j",
                    "error": error,
                },
            ),
        )
        await publish_build_state(
            config_id, BuildState.FAILED.value, build_id=build_id, channel=self._channel_fn(config_id)
        )
        # Audit C1: FAILED is a terminal state — make it durable immediately.
        await self._db.commit()

    async def _embed_entities(
        self,
        *,
        cfg: ConfigLike,
        build_id: uuid.UUID,
        triples: Sequence[Triple],
    ) -> tuple[list[EntityEmbedding], ResolvedEmbedder | None]:
        """Build a description per unique entity and embed them in a batch.

        Returns the embeddings and the resolved embedder identity so the caller
        can self-pin a null-pin config (D2). ``resolved`` is ``None`` only when
        there are no entities to embed.
        """
        pairs = build_entity_descriptions((t.subject, t.relation, t.object) for t in triples)
        if not pairs:
            return [], None
        descriptions = [desc for _, desc in pairs]
        resolved = await self._embedder_factory(cfg)
        vectors = await resolved.embedder.embed_batch(descriptions)
        if len(vectors) != len(descriptions):
            # DOM-5: a short embedding list would silently drop entities —
            # description rows with no Qdrant vector. A `strict=False` zip
            # would stop short and under-report `entities_written`. Fail
            # the build instead so the reconciler/operator sees it.
            raise GraphRagBuildFailed(
                f"embedder returned {len(vectors)} vectors for {len(descriptions)} entities"
            )
        return [
            EntityEmbedding(
                point_id=deterministic_point_id(cfg.id, build_id, entity),
                entity=entity,
                description=desc,
                vector=vec,
            )
            for (entity, desc), vec in zip(pairs, vectors, strict=True)
        ], resolved


# ---------------------------------------------------------------------------
# Collaborator protocols — declared here so the builder's import surface is
# self-contained without dragging concrete clients into the app layer.
# ---------------------------------------------------------------------------

from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: E402
from typing import Protocol  # noqa: E402


class DeltaLoader(Protocol):
    def iter_windows(
        self,
        *,
        config_id: uuid.UUID,
        since: Any,
        mode: Literal["delta", "full"],
    ) -> AsyncIterator[list[DeltaMessage]]:
        """Yield the delta as bounded windows (D1); one commit spans all windows."""
        ...


class _Embedder(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class ResolvedEmbedder:
    """An embedder plus the embedding identity it resolved to.

    Surfacing the resolved ``(provider, model)`` lets the builder self-pin a
    legacy null-pin config after its first successful embed (Phase 2a D2): the
    dimension is ``len(vector)`` and the provider/model are recorded so later
    builds select the same key deterministically.
    """

    embedder: _Embedder
    provider: str
    model: str


EmbedderFactory = Callable[[ConfigLike], Awaitable[ResolvedEmbedder]]


__all__ = [
    "LOCK_TTL_S",
    "MAX_DESC_FRAGMENTS",
    "SNAPSHOT_TTL_S",
    "DeltaLoader",
    "EmbedderFactory",
    "EntityEmbedding",
    "GraphRagBuilder",
    "ResolvedEmbedder",
    "attach_member_provenance",
    "attach_temporal_provenance",
    "build_entity_descriptions",
]
