"""GraphRAG config CRUD use-cases (E.7 / R11.01, R11.05, R11a.02).

The service owns validation (builder key-group belongs to the config's
project, 1:1 with agent guaranteed by the DB UNIQUE), audit emission,
soft-delete, and external store cascade (Neo4j + Qdrant teardown).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.infrastructure import tables as ag
from contexts.conversation.infrastructure import tables as conv_t
from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.application.embed_resolution import resolve_embed_key
from contexts.knowledge.application.graph_admin_reset import (
    GraphResetBinding,
    perform_admin_reset,
)
from contexts.knowledge.domain.embedding_pin import PinKind, TeardownOutcome
from contexts.knowledge.domain.errors import (
    GraphRagBuilderKeyGroupProjectMismatch,
    GraphRagConfigNotFound,
    GraphRagEmbedDimensionConflict,
    GraphRagEmbeddingModelChangeBlocked,
    GraphRagInvalidHalfLife,
    GraphRagOwnerProjectMismatch,
    GraphRagResetCompensationFailed,
)
from contexts.knowledge.domain.graphrag import (
    GraphRagConfig,
    GraphRagConfigDraft,
)
from contexts.knowledge.domain.models import embed_dimension
from contexts.knowledge.infrastructure import graphrag_tables as gt
from contexts.knowledge.infrastructure.embedding_pin_repository import EmbeddingPinRepository
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from shared_kernel import audit

if TYPE_CHECKING:
    from contexts.knowledge.application.graphrag_ports import (
        BuildLockStore,
        Neo4jDriver,
        SnapshotStore,
    )
    from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore

_log = logging.getLogger(__name__)


def _validate_half_life(value: float | None) -> None:
    """Reject a non-positive recency half-life (WS5, R11.21). ``None`` is allowed."""
    if value is not None and value <= 0:
        raise GraphRagInvalidHalfLife(f"recency_half_life_days must be > 0, got {value}")


async def resolve_live_builder_group_project_id(
    db: AsyncSession, builder_key_group_id: uuid.UUID
) -> uuid.UUID | None:
    """Return a builder Key Group's ``project_id`` if it is live, else ``None``.

    R7.09a: the single predicate for "is this builder key group live", shared by
    ``create``/``update`` (validated on write) and :class:`GraphRagBuilder`'s
    dispatch-time pre-flight (validated again at build time), so the check is not
    written a fourth time. Reads through `KeysFacade.get_key_group` rather than a
    raw `sa.select` against the keys context's tables, so this cross-context read
    goes through the facade contract like every other caller of this predicate.
    """
    group = await KeysFacade(db).get_key_group(builder_key_group_id)
    return group.project_id if group is not None else None


class GraphRagConfigService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        snapshot_store: SnapshotStore | None = None,
        lock_store: BuildLockStore | None = None,
        neo4j: Neo4jDriver | None = None,
    ) -> None:
        self._db = db
        self._configs = GraphRagConfigRepository(db)
        self._pins = EmbeddingPinRepository(db)
        # F-26: injectable external-store ports for admin_reset compensation. Left
        # None on the normal request path — admin_reset builds short-lived Redis
        # stores + a Neo4j driver itself (mirroring cascade_external_stores); only
        # tests override these via the attribute-patch pattern.
        self._snapshots = snapshot_store
        self._locks = lock_store
        self._neo4j = neo4j

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: GraphRagConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        # Phase 2b WS2: owner-centric. The discriminated owner must already exist
        # and live in this project (the former agent-centric auto-singleton wrap
        # is retired; an agent_group owner is created + populated via the
        # member-CRUD surface first). _assert_owner_in_project handles all three
        # owner kinds.
        await self._assert_owner_in_project(
            owner_kind=draft.owner_kind, owner_id=draft.owner_id, project_id=project_id
        )
        _validate_half_life(draft.recency_half_life_days)

        builder_group_project_id = await resolve_live_builder_group_project_id(
            self._db, draft.builder_key_group_id
        )
        if builder_group_project_id is None or builder_group_project_id != project_id:
            raise GraphRagBuilderKeyGroupProjectMismatch(
                f"builder_key_group_id {draft.builder_key_group_id} "
                f"does not belong to project {project_id}"
            )

        # Phase 2a D2: derive + enforce the project embedding pin from the builder
        # key group. If the group has no embedding key yet the pin is left null and
        # the config self-pins on its first successful build.
        pin = await self._enforce_and_resolve_pin(project_id, draft.builder_key_group_id)
        embed_provider, embed_model, embed_dim = pin if pin is not None else (None, None, None)

        # Durable, race-free project dimension pin (F-11). A group with no
        # embedding key resolves to a null pin — nothing to serialize until a
        # sibling first pins the project. When a dimension resolves, the pin table
        # is authoritative and its advisory lock closes the concurrent-first-create
        # race the ``_project_pinned_dim`` scan alone cannot.
        if pin is not None:
            # F-3: retry a teardown a previous delete left owed, so this create is
            # not rejected by a pin whose collection nobody is using. No Qdrant call
            # unless the pin is a different dimension and the project is configless.
            await self._retry_pending_teardown(project_id, pin[2])
            await self._pins.ensure(
                project_id=project_id,
                kind=PinKind.GRAPHRAG,
                provider=pin[0],
                model=pin[1],
                dim=pin[2],
                on_conflict=lambda existing_dim, this_dim: GraphRagEmbedDimensionConflict(
                    f"project {project_id} is pinned to {existing_dim}-dim embeddings; "
                    f"builder key group {draft.builder_key_group_id} resolves to {this_dim}-dim"
                ),
            )

        cfg = await self._configs.create(
            project_id=project_id,
            owner_kind=draft.owner_kind,
            owner_id=draft.owner_id,
            builder_key_group_id=draft.builder_key_group_id,
            trigger_config=draft.trigger_config,
            embed_provider=embed_provider,
            embed_model=embed_model,
            embed_dim=embed_dim,
            recency_half_life_days=draft.recency_half_life_days,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "owner_kind": draft.owner_kind,
                    "owner_id": str(draft.owner_id),
                    "builder_key_group_id": str(draft.builder_key_group_id),
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def _resolve_group_pin(
        self,
        builder_key_group_id: uuid.UUID,
    ) -> tuple[str, str, int] | None:
        """Resolve the builder key group to ``(provider, model, dim)`` (D2).

        Returns ``None`` when the group has no actively-carried embedding key, so
        the caller leaves the pin null and the config self-pins on first build.
        """
        resolved = await resolve_embed_key(self._db, builder_key_group_id)
        if resolved is None:
            return None
        provider, model, _key_id = resolved
        return provider, model, embed_dimension(provider, model)

    async def _project_pinned_dim(
        self,
        project_id: uuid.UUID,
        *,
        exclude_config_id: uuid.UUID | None = None,
    ) -> int | None:
        """The project's already-pinned embedding dimension, if any (D2).

        Read from a sibling config's persisted ``embed_dim`` (Postgres-only, so
        config CRUD never depends on Qdrant availability). The build-time
        collection-dimension guard (D7) is the backstop for the transitional case
        where a project has a built collection but no yet-pinned sibling.
        """
        stmt = sa.select(gt.graphrag_configs.c.embed_dim).where(
            sa.and_(
                gt.graphrag_configs.c.project_id == project_id,
                gt.graphrag_configs.c.embed_dim.isnot(None),
                gt.graphrag_configs.c.deleted_at.is_(None),
            )
        )
        if exclude_config_id is not None:
            stmt = stmt.where(gt.graphrag_configs.c.id != exclude_config_id)
        row = (await self._db.execute(stmt.limit(1))).first()
        return int(row.embed_dim) if row is not None else None

    async def _enforce_and_resolve_pin(
        self,
        project_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        *,
        exclude_config_id: uuid.UUID | None = None,
    ) -> tuple[str, str, int] | None:
        """Resolve the group pin and reject it if it conflicts with the project.

        Returns the resolved ``(provider, model, dim)`` to persist, or ``None``
        when the group yields no embedding key. Raises
        :class:`GraphRagEmbedDimensionConflict` (422) when the resolved dimension
        differs from the project's existing pin (R11.18).
        """
        pin = await self._resolve_group_pin(builder_key_group_id)
        if pin is None:
            return None
        _, _, dim = pin
        existing = await self._project_pinned_dim(project_id, exclude_config_id=exclude_config_id)
        if existing is not None and existing != dim:
            raise GraphRagEmbedDimensionConflict(
                f"project {project_id} is pinned to {existing}-dim embeddings; "
                f"builder key group {builder_key_group_id} resolves to {dim}-dim"
            )
        return pin

    async def _assert_owner_in_project(
        self,
        *,
        owner_kind: str,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> None:
        """Reject an owner entity not in the config's project (D6, AC-9).

        Dispatch per ``owner_kind``: ``agent_group`` and ``workspace`` carry
        ``project_id`` directly; ``chatroom`` reaches it via its workspace
        (2-hop). Raises :class:`GraphRagOwnerProjectMismatch` (422) on a project
        mismatch or a missing/deleted owner. Implemented for every kind now even
        though Phase 1 create only exercises ``agent_group`` — Phase 2b adds the
        chatroom/workspace owner surfaces and this guard is already in place.
        """
        if owner_kind == "agent_group":
            stmt = sa.select(ag.agent_groups.c.project_id).where(
                sa.and_(
                    ag.agent_groups.c.id == owner_id,
                    ag.agent_groups.c.deleted_at.is_(None),
                )
            )
        elif owner_kind == "workspace":
            stmt = sa.select(conv_t.workspaces.c.project_id).where(
                sa.and_(
                    conv_t.workspaces.c.id == owner_id,
                    conv_t.workspaces.c.deleted_at.is_(None),
                )
            )
        elif owner_kind == "chatroom":
            stmt = (
                sa.select(conv_t.workspaces.c.project_id)
                .select_from(
                    conv_t.chatrooms.join(
                        conv_t.workspaces,
                        conv_t.workspaces.c.id == conv_t.chatrooms.c.workspace_id,
                    )
                )
                .where(
                    sa.and_(
                        conv_t.chatrooms.c.id == owner_id,
                        conv_t.chatrooms.c.deleted_at.is_(None),
                        conv_t.workspaces.c.deleted_at.is_(None),
                    )
                )
            )
        else:
            raise GraphRagOwnerProjectMismatch(f"unknown owner_kind {owner_kind!r}")

        row = (await self._db.execute(stmt)).first()
        if row is None or row.project_id != project_id:
            raise GraphRagOwnerProjectMismatch(f"{owner_kind} {owner_id} is not in project {project_id}")

    async def _config_owner(self, config_id: uuid.UUID) -> tuple[str, uuid.UUID]:
        """Return the config's discriminated owner ``(owner_kind, owner_id)``."""
        row = (
            await self._db.execute(
                sa.select(
                    gt.graphrag_configs.c.owner_kind,
                    gt.graphrag_configs.c.owner_chatroom_id,
                    gt.graphrag_configs.c.owner_agent_group_id,
                    gt.graphrag_configs.c.owner_workspace_id,
                ).where(gt.graphrag_configs.c.id == config_id)
            )
        ).one()
        owner_id = {
            "chatroom": row.owner_chatroom_id,
            "agent_group": row.owner_agent_group_id,
            "workspace": row.owner_workspace_id,
        }[row.owner_kind]
        return cast(str, row.owner_kind), cast(uuid.UUID, owner_id)

    async def get(self, config_id: uuid.UUID) -> GraphRagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise GraphRagConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        return await self._configs.list_for_project(project_id)

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        cfg = await self.get(config_id)
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={"project_id": str(cfg.project_id)},
                request_id=request_id,
            ),
        )
        return cfg

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID | None,
        trigger_config: dict[str, Any] | None,
        recency_half_life_days: float | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        """R11.05 partial update — edit trigger config, builder key group, or the
        recency half-life (WS5).

        Re-checks that a swapped builder key group still lives in the config's
        project. The former builder-vs-agent-consumer distinctness check is
        dropped in Phase 1: ownership is an agent_group, not a single agent, so
        there is no per-agent consumer key group for the builder to collide with.
        A provided ``recency_half_life_days`` is validated positive; ``None``
        leaves it unchanged (patch semantics).
        """
        cfg = await self.get(config_id)
        _validate_half_life(recency_half_life_days)
        # D6: owner is immutable post-create, but re-assert defensively that it
        # still lives in the config's project before any mutation.
        owner_kind, owner_id = await self._config_owner(config_id)
        await self._assert_owner_in_project(
            owner_kind=owner_kind, owner_id=owner_id, project_id=cfg.project_id
        )
        group_changed = builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id
        new_pin: tuple[str, str, int] | None = None
        if group_changed:
            # group_changed is True only when builder_key_group_id is not None.
            assert builder_key_group_id is not None
            builder_group_project_id = await resolve_live_builder_group_project_id(
                self._db, builder_key_group_id
            )
            if builder_group_project_id is None or builder_group_project_id != cfg.project_id:
                raise GraphRagBuilderKeyGroupProjectMismatch(
                    f"builder_key_group_id {builder_key_group_id} "
                    f"does not belong to project {cfg.project_id}"
                )
            # Phase 2a D2: a swapped builder group must still resolve to the
            # project's pinned dimension; re-derive and persist the new pin. The
            # dimension conflict (raised inside _enforce_and_resolve_pin) keeps
            # precedence over the F-13 model-change guard below.
            new_pin = await self._enforce_and_resolve_pin(
                cfg.project_id, builder_key_group_id, exclude_config_id=config_id
            )
            # F-13 (R11.19): a swap to a different embedding provider/model
            # invalidates the config's existing vector space just as a dimension
            # change does — same dimension, different model is a semantically
            # incompatible space. Reject it while the config holds indexed vectors
            # (fail-closed on any prior build); a never-built config may change
            # freely. Guard on the *resolved* (provider, model), so a group change
            # that keeps the same embedding (e.g. only the extraction LLM differs)
            # stays allowed. The graphrag pin is nullable — a group with no
            # embedding key yields new_pin=None and has no space to invalidate.
            if (
                new_pin is not None
                and (new_pin[0], new_pin[1]) != (cfg.embed_provider, cfg.embed_model)
                and cfg.last_build_at is not None
            ):
                raise GraphRagEmbeddingModelChangeBlocked(
                    f"config {config_id} has indexed vectors under "
                    f"{cfg.embed_provider}:{cfg.embed_model}; changing the embedding model to "
                    f"{new_pin[0]}:{new_pin[1]} would invalidate them — clear and recreate to "
                    f"change the embedding model"
                )

        await self._configs.update(
            config_id=config_id,
            builder_key_group_id=builder_key_group_id,
            trigger_config=trigger_config,
            recency_half_life_days=recency_half_life_days,
        )
        if group_changed and new_pin is not None:
            await self._configs.set_embed_pin(
                config_id=config_id,
                provider=new_pin[0],
                model=new_pin[1],
                dim=new_pin[2],
            )
            # Keep the durable F-11 pin consistent with the config's new embedding
            # (reached only on an allowed change — a blocked model swap raised
            # above). The update-path dimension guard has ensured no live sibling
            # pins a different dimension, so overwriting is safe.
            await self._pins.upsert(
                project_id=cfg.project_id,
                kind=PinKind.GRAPHRAG,
                provider=new_pin[0],
                model=new_pin[1],
                dim=new_pin[2],
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="graphrag.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="graphrag_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "builder_key_group_id_changed": (
                        builder_key_group_id is not None and builder_key_group_id != cfg.builder_key_group_id
                    ),
                    "trigger_config_changed": trigger_config is not None,
                    "recency_half_life_days_changed": recency_half_life_days is not None,
                },
                request_id=request_id,
            ),
        )
        refreshed = await self._configs.get(config_id)
        assert refreshed is not None
        return refreshed

    async def admin_reset(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        force: bool = False,
        request_id: uuid.UUID | None = None,
    ) -> GraphRagConfig:
        """R11a.02 — reset a stuck config to ``idle`` after compensating 2PC state (F-26).

        Thin binding of :func:`perform_admin_reset` to the Concept Map's repository,
        audit identity, and error class; see that function for the full contract. The
        Knowledge Map binds the same implementation, so the two products' resets cannot
        drift apart.
        """
        from contexts.knowledge.infrastructure.redis_lock import (
            RedisBuildLockStore,
            RedisSnapshotStore,
        )

        cfg = await self.get(config_id)

        await perform_admin_reset(
            self._db,
            config_id=config_id,
            project_id=cfg.project_id,
            prev=cfg.last_build_state,
            binding=GraphResetBinding(
                configs=self._configs,
                audit_action="admin.graphrag_reset",
                resource_type="graphrag_config",
                compensation_error=GraphRagResetCompensationFailed,
            ),
            snapshots=self._snapshots or RedisSnapshotStore(),
            locks=self._locks or RedisBuildLockStore(),
            neo4j=self._neo4j,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            force=force,
            request_id=request_id,
        )

        refreshed = await self._configs.get(config_id)
        assert refreshed is not None
        return refreshed

    async def status(
        self,
        config_id: uuid.UUID,
    ) -> dict[str, Any]:
        cfg = await self.get(config_id)
        return {
            "id": str(cfg.id),
            "state": cfg.last_build_state.value,
            "last_build_at": (cfg.last_build_at.isoformat() if cfg.last_build_at else None),
            "last_build_error": cfg.last_build_error,
        }

    async def teardown_orphan_collection(self, *, project_id: uuid.UUID) -> TeardownOutcome:
        """Drop ``graphrag_{project_id}`` and release the pin, iff configless (F-3/F-11).

        The Concept Map counterpart of
        :meth:`contexts.knowledge.application.config_service.RagConfigService.teardown_orphan_collection`
        — see that docstring for why the pin now trails the drop (F-3) and for the
        deliberate W5 trade (spec Q-4). The pin is released only on a confirmed
        ``DROPPED``/``ABSENT``; a Qdrant error keeps it so an incompatible builder Key
        Group stays rejected while the old-dimension collection is still standing.
        """
        await self._pins.acquire_lock(project_id, PinKind.GRAPHRAG)
        if list(await self._configs.list_for_project(project_id)):
            return TeardownOutcome.SKIPPED_LIVE_CONFIG

        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
        from contexts.knowledge.infrastructure.qdrant_teardown import delete_collection_bounded

        try:
            dropped = await delete_collection_bounded(
                lambda client: GraphRagVectorStore(client).delete_collection(project_id)
            )
        except Exception as exc:
            # The exception class only — qdrant-client errors carry response content
            # and headers. See RagConfigService.teardown_orphan_collection.
            _log.error(
                "graphrag teardown: qdrant collection drop failed for project %s (%s); "
                "pin retained, collection will be retried",
                project_id,
                type(exc).__name__,
            )
            return TeardownOutcome.FAILED

        await self._pins.clear(project_id=project_id, kind=PinKind.GRAPHRAG)
        return TeardownOutcome.DROPPED if dropped else TeardownOutcome.ABSENT

    async def _retry_pending_teardown(self, project_id: uuid.UUID, new_dim: int) -> None:
        """Retry a teardown a previous delete left owed (F-3, spec Q-5).

        See
        :meth:`contexts.knowledge.application.config_service.RagConfigService._retry_pending_teardown`,
        including why the lock is taken with *try* rather than blocking (FU-3). Fires
        only for a different-dimension create against a configless retained pin; a
        matching-dimension create issues no Qdrant call.
        """
        if not await self._pins.try_acquire_lock(project_id, PinKind.GRAPHRAG):
            return
        pin = await self._pins.get(project_id, PinKind.GRAPHRAG)
        if pin is None or int(pin.dim) == new_dim:
            return
        await self.teardown_orphan_collection(project_id=project_id)

    # ---- infrastructure cascade -------------------------------------------

    @staticmethod
    async def cascade_external_stores(
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict[str, bool]:
        """Best-effort removal of Neo4j subgraph + Qdrant entity vectors.

        DOM-4: must be called only *after* the soft-delete + audit row have
        been committed. Builds its own short-lived clients (the request path
        owns no long-lived ones) and delegates the actual teardown to
        :func:`purge_config_external_stores`, which the reconciler sweep also
        uses with its own injected clients. Returns a summary dict for the
        follow-up audit row.
        """
        from app.config.settings import get_settings

        settings = get_settings()

        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        neo4j_conf = getattr(settings, "neo4j", None)
        driver = (
            Neo4jAsyncDriver(uri=neo4j_conf.url, auth=(neo4j_conf.user, neo4j_conf.password))
            if neo4j_conf is not None
            else None
        )

        from qdrant_client import AsyncQdrantClient

        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore

        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        try:
            return await purge_config_external_stores(
                config_id=config_id,
                project_id=project_id,
                neo4j=driver,
                vectors=GraphRagVectorStore(qclient),
            )
        finally:
            if driver is not None:
                await driver.close()
            await qclient.close()


async def purge_config_external_stores(
    *,
    config_id: uuid.UUID,
    project_id: uuid.UUID | None,
    neo4j: Neo4jDriver | None,
    vectors: GraphRagVectorStore | None,
) -> dict[str, bool]:
    """Delete a config's Neo4j subgraph (section 22.8) + Qdrant vectors (DOM-2).

    The single teardown both the request-path cascade and the reconciler orphan
    sweep share, using caller-owned clients. Each store is best-effort and
    isolated: a failure on one is reported in the summary dict without aborting
    the other. ``project_id`` may be ``None`` for a legacy orphan whose nodes
    predate the self-describing property; the Qdrant collection is
    project-scoped, so that case is reported ``qdrant_purged=False`` (FU-B).
    """
    neo4j_purged = False
    qdrant_purged = False
    if neo4j is not None:
        try:
            await neo4j.delete_all(config_id=config_id)
            neo4j_purged = True
        except Exception:
            _log.exception("graphrag delete: neo4j cascade failed for config %s", config_id)
    if project_id is not None and vectors is not None:
        try:
            await vectors.delete_by_config(project_id=project_id, config_id=config_id)
            qdrant_purged = True
        except Exception:
            _log.exception("graphrag delete: qdrant cascade failed for config %s", config_id)
    return {"neo4j_purged": neo4j_purged, "qdrant_purged": qdrant_purged}


__all__ = ["GraphRagConfigService", "purge_config_external_stores"]
