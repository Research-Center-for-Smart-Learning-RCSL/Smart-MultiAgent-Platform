"""Shared GraphRAG purge cascade for owner deletes (Phase 2b WS6, R11.20/AC-10).

Deleting a chatroom, workspace, or agent_group must purge the Neo4j subgraph and
Qdrant points of any Concept Map it owns, audit-logged — never relying on the DB
cascade alone (the external stores have no FK to the owner row). The agent-delete
route (``agents.py``) shares the purge half too: a config resolved via
``list_for_agents`` may belong to a DIFFERENT agent_group per row (each config's
own ``owner_*`` id is used for its audit metadata), unlike the other three routes
where every config in the list shares one caller-known owner.

The two-phase shape mirrors the agent route and honours DOM-4: soft-deletes run
in the caller's open transaction (committed by the route alongside the owner's own
soft-delete), and the external-store purge runs strictly *after* that commit.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.interfaces.facade import KnowledgeFacade

_log = logging.getLogger(__name__)


def _owner_id_of(cfg: GraphRagConfig) -> uuid.UUID | None:
    return {
        "chatroom": cfg.owner_chatroom_id,
        "agent_group": cfg.owner_agent_group_id,
        "workspace": cfg.owner_workspace_id,
    }.get(cfg.owner_kind)


async def soft_delete_owner_graph_configs(
    facade: KnowledgeFacade,
    *,
    owner_kind: str,
    owner_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
) -> list[GraphRagConfig]:
    """Enumerate and soft-delete the configs owned by ``owner_id`` (no commit).

    Returns the configs so the caller can purge their external stores after the
    transaction commits (DOM-4). Each soft-delete emits the canonical
    ``graphrag.deleted`` audit event via the config service.
    """
    configs = list(await facade.list_graph_configs_for_owner(owner_kind=owner_kind, owner_id=owner_id))
    for cfg in configs:
        await facade.soft_delete_graph_config(
            cfg.id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
    return configs


async def purge_owner_graph_configs_external(
    db: AsyncSession,
    *,
    configs: Sequence[GraphRagConfig],
    actor_user_id: uuid.UUID,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
    extra_metadata: dict[str, str] | None = None,
) -> None:
    """Purge each config's Neo4j subgraph + Qdrant points and audit it (post-commit).

    Each config's own ``owner_kind``/owner id is used for its audit metadata —
    NOT a single blanket owner passed by the caller — so this works whether every
    config in the list shares one owner (a chatroom/workspace/agent_group delete)
    or spans several different owners (an agent-delete cascade, where each
    returned config may belong to a different agent_group). ``extra_metadata`` is
    merged into every config's audit event (e.g. the deleted agent's id).

    Best-effort per config: the purge already swallows its own store errors, and
    the audit write is guarded too so an emit failure can never turn a durably
    committed owner delete into a 500 (nor skip a sibling config). Must be called
    only after the owner + config soft-deletes have been committed.
    """
    from shared_kernel import audit as _audit

    for cfg in configs:
        try:
            outcome = await KnowledgeFacade.purge_graph_config_external_stores(
                config_id=cfg.id,
                project_id=cfg.project_id,
            )
            owner_id = _owner_id_of(cfg)
            await _audit.emit(
                db,
                _audit.AuditEvent(
                    action="graphrag.config_infra_purged",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="graphrag_config",
                    resource_id=cfg.id,
                    metadata={
                        "project_id": str(cfg.project_id),
                        "owner_kind": cfg.owner_kind,
                        "owner_id": str(owner_id) if owner_id else None,
                        **(extra_metadata or {}),
                        **outcome,
                    },
                    request_id=request_id,
                ),
            )
            # Commit each config's purge-audit row independently. The external
            # purge is destructive and already done, so its audit must survive a
            # later sibling's failure — a single shared transaction with a
            # mid-loop rollback would discard the already-recorded siblings.
            await db.commit()
        except Exception:
            _log.exception("graphrag delete: post-commit purge/audit failed for config %s", cfg.id)
            await db.rollback()


__all__ = [
    "purge_owner_graph_configs_external",
    "soft_delete_owner_graph_configs",
]
