"""Rebuild every agent_group Concept Map in place, so its provenance is current.

``docs/tasks/2026-10-12-graphrag-phase5b-member-filter`` changed what a group
map records on each relation (R11.22): the authoring member for a member
agent's message, every member present for any other message. Relations built
before that keep the old single-member value, and an ordinary delta build never
rewrites them, so a member read filter would act on misleading provenance. This
command enqueues one full replace build per live agent_group map, which
re-extracts its whole history.

Cost: every listed map re-extracts its history on the project's builder key. A
dry run lists the maps with their message counts and enqueues nothing; it is
armed by ``SMAP_REBUILD_AGENT_GROUP_CONCEPT_MAPS_ARMED`` (see
``purge_session_dirs._ARMED_ENV`` for why that is not a flag). Each run uses a
job id of its own, so a rebuild is never mistaken for an ordinary build of the
same map; a map already building makes its rebuild wait, not fail.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from app.workers.agent_fs_gc import _TRUTHY

_log = logging.getLogger(__name__)

ARMED_ENV = "SMAP_REBUILD_AGENT_GROUP_CONCEPT_MAPS_ARMED"


@dataclass(frozen=True, slots=True)
class GroupMap:
    config_id: uuid.UUID
    project_id: uuid.UUID
    agent_group_id: uuid.UUID
    messages: int


@dataclass(frozen=True, slots=True)
class RebuildReport:
    dry_run: bool
    maps: tuple[GroupMap, ...]
    enqueued: int = 0
    deduped: int = 0
    failed: tuple[uuid.UUID, ...] = ()


def is_armed() -> bool:
    return os.environ.get(ARMED_ENV, "").strip().lower() in _TRUTHY


def rebuild_job_id(config_id: uuid.UUID, run_stamp: int) -> str:
    return f"graphrag:rebuild:{config_id}:{run_stamp}"


async def _count_messages(db: Any, member_ids: list[uuid.UUID]) -> int:
    """Messages a full build of the group would read: its live members' rooms.

    Mirrors the agent_group scope of ``_resolve_delta_scope`` in
    ``app/workers/tasks/graphrag.py``, the source of truth; a change there
    changes what this estimate should count.
    """
    if not member_ids:
        return 0
    from contexts.conversation.infrastructure import tables as conv

    m, cr, ca = conv.messages, conv.chatrooms, conv.chatroom_agents
    stmt = (
        sa.select(sa.func.count(sa.distinct(m.c.id)))
        .select_from(m.join(cr, cr.c.id == m.c.chatroom_id).join(ca, ca.c.chatroom_id == cr.c.id))
        .where(
            sa.and_(
                ca.c.agent_id.in_(member_ids),
                m.c.deleted_at.is_(None),
                cr.c.deleted_at.is_(None),
            )
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_group_maps() -> tuple[GroupMap, ...]:
    from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
    from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
    from shared_kernel.db.session import get_sessionmaker

    async with get_sessionmaker()() as db:
        configs = await GraphRagConfigRepository(db).list_live_agent_group_configs()
        groups = AgentGroupRepository(db)
        maps: list[GroupMap] = []
        for cfg in configs:
            if cfg.owner_agent_group_id is None:  # excluded by the query's owner_kind
                continue
            members = list(await groups.list_member_agent_ids(cfg.owner_agent_group_id))
            maps.append(
                GroupMap(
                    config_id=cfg.id,
                    project_id=cfg.project_id,
                    agent_group_id=cfg.owner_agent_group_id,
                    messages=await _count_messages(db, members),
                )
            )
    return tuple(maps)


async def run(*, pool: Any = None) -> RebuildReport:
    maps = await list_group_maps()
    if not is_armed():
        return RebuildReport(dry_run=True, maps=maps)

    owned = pool is None
    if pool is None:
        from arq.connections import RedisSettings, create_pool

        from app.config.settings import get_settings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
    run_stamp = int(time.time())
    enqueued = deduped = 0
    failed: list[uuid.UUID] = []
    try:
        for gm in maps:
            try:
                job = await pool.enqueue_job(
                    "graphrag_build",
                    config_id=str(gm.config_id),
                    triggered_by="provenance_rebuild",
                    full_rebuild=True,
                    _job_id=rebuild_job_id(gm.config_id, run_stamp),
                )
            except Exception:
                _log.exception("rebuild-agent-group-concept-maps: enqueue failed for config %s", gm.config_id)
                failed.append(gm.config_id)
                continue
            if job is None:
                deduped += 1
            else:
                enqueued += 1
    finally:
        if owned:
            await pool.close(close_connection_pool=True)
    return RebuildReport(dry_run=False, maps=maps, enqueued=enqueued, deduped=deduped, failed=tuple(failed))


__all__ = ["ARMED_ENV", "GroupMap", "RebuildReport", "is_armed", "rebuild_job_id", "run"]
