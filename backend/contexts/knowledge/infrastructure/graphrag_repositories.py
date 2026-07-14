"""Async repository for ``graphrag_configs`` (E.7).

All writes keep the caller's :class:`AsyncSession` transaction — the
service layer owns commit/rollback semantics so audit rows and state
transitions stay atomic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.infrastructure import tables as ag
from contexts.agents.infrastructure import tables as agents_t
from contexts.conversation.infrastructure import tables as conv
from contexts.knowledge.application.graphrag_ports import GraphRagConfigRepositoryPort
from contexts.knowledge.domain.errors import GraphRagConfigAlreadyExists
from contexts.knowledge.domain.graphrag import (
    AgentConceptMapCoverage,
    BuildState,
    ConceptMapOwnerOption,
    GraphRagConfig,
)
from contexts.knowledge.infrastructure import graphrag_tables as t
from shared_kernel.auth.clients import now


def _member_agent_id() -> Any:
    """Scalar subquery deriving the owning agent from the singleton group member.

    Phase 1 dropped ``graphrag_configs.agent_id`` (0044); the owning agent is
    now derived from the owner ``agent_group``'s membership. For a Phase-1
    singleton group there is exactly one member, so this is deterministic and
    equals the former column. ``LIMIT 1`` keeps it single-valued once multi-
    member groups arrive (Phase 2b); a non-``agent_group`` owner yields NULL.
    """
    m = ag.agent_group_members
    return (
        sa.select(m.c.agent_id)
        .where(m.c.agent_group_id == t.graphrag_configs.c.owner_agent_group_id)
        .limit(1)
        # Correlate ONLY graphrag_configs. In list_for_agents the outer query
        # also joins agent_group_members, and without this SQLAlchemy would
        # auto-correlate the subquery's own agent_group_members away, leaving it
        # with no FROM clause (InvalidRequestError). Pinning correlation keeps
        # the subquery's member scan local in every call site.
        .correlate(t.graphrag_configs)
        .scalar_subquery()
        .label("agent_id")
    )


def _owner_name() -> Any:
    """Coalesced display name of a config's discriminated owner (Phase 4α).

    Exactly one ``owner_*_id`` is set per config, so the COALESCE of three
    correlated scalar subqueries yields that owner's name without a join fan-out.
    Lets the project-scoped overview render owner names without the agents slice
    importing the downstream conversation / agent_groups slices.
    """
    gc = t.graphrag_configs
    chatroom = (
        sa.select(conv.chatrooms.c.name)
        .where(conv.chatrooms.c.id == gc.c.owner_chatroom_id)
        .correlate(gc)
        .scalar_subquery()
    )
    group = (
        sa.select(ag.agent_groups.c.name)
        .where(ag.agent_groups.c.id == gc.c.owner_agent_group_id)
        .correlate(gc)
        .scalar_subquery()
    )
    workspace = (
        sa.select(conv.workspaces.c.name)
        .where(conv.workspaces.c.id == gc.c.owner_workspace_id)
        .correlate(gc)
        .scalar_subquery()
    )
    return sa.func.coalesce(chatroom, group, workspace).label("owner_name")


def _config_select() -> Any:
    """SELECT over ``graphrag_configs`` with the derived agent + owner-name columns."""
    return sa.select(t.graphrag_configs, _member_agent_id(), _owner_name())


def _row_to_config(row: Any) -> GraphRagConfig:
    return GraphRagConfig(
        id=row.id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        builder_key_group_id=row.builder_key_group_id,
        trigger_config=dict(row.trigger_config or {}),
        last_build_at=row.last_build_at,
        last_build_state=BuildState(row.last_build_state),
        last_build_error=row.last_build_error,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        embed_provider=row.embed_provider,
        embed_model=row.embed_model,
        embed_dim=row.embed_dim,
        owner_kind=row.owner_kind,
        owner_chatroom_id=row.owner_chatroom_id,
        owner_agent_group_id=row.owner_agent_group_id,
        owner_workspace_id=row.owner_workspace_id,
        recency_half_life_days=row.recency_half_life_days,
        # Present only on selects built via _config_select(); other reads (turn
        # resolver, membership feed) omit it and leave the display name None.
        owner_name=getattr(row, "owner_name", None),
    )


_OWNER_COLUMN = {
    "chatroom": "owner_chatroom_id",
    "agent_group": "owner_agent_group_id",
    "workspace": "owner_workspace_id",
}

# Narrow -> wide precedence for the layered resolver (WS4 R11.09).
_LAYER_RANK = {"chatroom": 0, "agent_group": 1, "workspace": 2}


class GraphRagConfigRepository(GraphRagConfigRepositoryPort):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        owner_kind: str,
        owner_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        trigger_config: dict[str, Any],
        embed_provider: str | None = None,
        embed_model: str | None = None,
        embed_dim: int | None = None,
        recency_half_life_days: float | None = None,
    ) -> GraphRagConfig:
        """Insert a config for a discriminated owner (Phase 2b WS2).

        Sets the single ``owner_*`` column matching ``owner_kind`` (the CHECK and
        partial-unique indexes from Phase 1 enforce exactly-one ownership and 1:1).
        """
        owner_col = _OWNER_COLUMN[owner_kind]
        try:
            await self._db.execute(
                t.graphrag_configs.insert().values(
                    project_id=project_id,
                    owner_kind=owner_kind,
                    builder_key_group_id=builder_key_group_id,
                    trigger_config=trigger_config,
                    embed_provider=embed_provider,
                    embed_model=embed_model,
                    embed_dim=embed_dim,
                    recency_half_life_days=recency_half_life_days,
                    **{owner_col: owner_id},
                )
            )
        except IntegrityError as exc:
            # The per-owner partial-unique enforces 1:1 ownership; a second create
            # for the same owner is a domain 409, not a 500.
            raise GraphRagConfigAlreadyExists(str(owner_id)) from exc
        row = (
            await self._db.execute(_config_select().where(t.graphrag_configs.c[owner_col] == owner_id))
        ).one()
        return _row_to_config(row)

    async def get(
        self,
        config_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> GraphRagConfig | None:
        pred: sa.ColumnElement[bool] = t.graphrag_configs.c.id == config_id
        if not include_deleted:
            pred = sa.and_(pred, t.graphrag_configs.c.deleted_at.is_(None))
        row = (await self._db.execute(_config_select().where(pred))).first()
        return _row_to_config(row) if row else None

    async def list_for_project(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                _config_select()
                .where(
                    sa.and_(
                        t.graphrag_configs.c.project_id == project_id,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.graphrag_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_for_owner(
        self,
        *,
        owner_kind: str,
        owner_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        """Live configs owned directly by a chatroom / workspace / agent_group.

        The owner-delete cascade (WS6 R11.20/AC-10) enumerates a deleted owner's
        configs so their Neo4j subgraph + Qdrant points can be purged. Selects on
        the single ``owner_*`` column matching ``owner_kind``; the per-owner
        partial-unique makes this at most one row, but a list keeps the call site
        symmetric with :meth:`list_for_agents` and robust to any future relaxation.
        Unlike ``list_for_agents`` this resolves ownership by the owner column, not
        the membership join — an agent_group owner is deleted as a whole here.
        """
        owner_col = getattr(t.graphrag_configs.c, _OWNER_COLUMN[owner_kind])
        rows = (
            await self._db.execute(
                _config_select()
                .where(
                    sa.and_(
                        t.graphrag_configs.c.owner_kind == owner_kind,
                        owner_col == owner_id,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.graphrag_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_for_agents(
        self,
        agent_ids: Sequence[uuid.UUID],
    ) -> Sequence[GraphRagConfig]:
        """Configs an agent-delete of ``agent_ids`` should soft-delete + purge.

        Resolves ownership through the membership join, not the legacy
        ``agent_id`` column — but ONLY returns a config when removing
        ``agent_ids`` would leave its owning group with zero remaining LIVE
        members. A shared (multi-member) agent_group's Concept Map belongs to
        the group, not to any one member; deleting one member must not destroy
        it out from under the others (it is deleted only via the group's own
        delete route). For a Phase-1 singleton group (one member = the former
        owning agent) this is unaffected: removing its sole member always
        leaves zero live members, so the config is still returned exactly as
        the pre-decouple ``WHERE agent_id IN (:ids)`` did.
        """
        ids = list(dict.fromkeys(agent_ids))
        if not ids:
            return []
        gc = t.graphrag_configs
        m = ag.agent_group_members
        joined = gc.join(ag.agent_groups, ag.agent_groups.c.id == gc.c.owner_agent_group_id).join(
            m, m.c.agent_group_id == ag.agent_groups.c.id
        )
        other_live_member = (
            sa.select(sa.literal(1))
            .select_from(m.join(agents_t.agents, agents_t.agents.c.id == m.c.agent_id))
            .where(
                sa.and_(
                    m.c.agent_group_id == ag.agent_groups.c.id,
                    m.c.agent_id.notin_(ids),
                    agents_t.agents.c.deleted_at.is_(None),
                )
            )
            .correlate(ag.agent_groups)
            .exists()
        )
        rows = (
            await self._db.execute(
                sa.select(gc, _member_agent_id())
                .select_from(joined)
                .where(
                    sa.and_(
                        m.c.agent_id.in_(ids),
                        gc.c.deleted_at.is_(None),
                        ag.agent_groups.c.deleted_at.is_(None),
                        sa.not_(other_live_member),
                    )
                )
                .order_by(gc.c.created_at.desc())
            )
        ).all()
        seen: set[uuid.UUID] = set()
        out: list[GraphRagConfig] = []
        for r in rows:
            if r.id in seen:
                continue
            seen.add(r.id)
            out.append(_row_to_config(r))
        return out

    async def list_message_trigger_configs_for_room(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> Sequence[GraphRagConfig]:
        """Every Concept Map whose typed owner covers ``chatroom_id`` for the
        room's bound ``agent_ids`` (F-3, R11.02/R11.08).

        The message-count trigger path's owner coverage — deliberately the same
        set the retrieval resolver :meth:`list_layers_for_turn` produces, but
        unioned across all the room's bound agents instead of one turn's agent,
        so "what a message-count build produces" matches "what a turn retrieves":

        - the chatroom-owned config for the room — always (retrieval never
          enable-gates the chatroom layer, `list_layers_for_turn` :325-331);
        - each ``agent_group``-owned config whose group has a **live** member in
          ``agent_ids`` and whose group has ``concept_map_enabled``. Shared
          groups with *other* surviving members are included — the opposite of
          :meth:`list_for_agents`, whose ``NOT EXISTS(other_live_member)``
          deletion predicate silently made every multi-member map inert here;
        - the workspace-owned config for the room's workspace when the workspace
          has ``concept_map_enabled``.

        NOT :meth:`list_for_agents`: that selector is the agent-delete cascade
        (joins only ``owner_agent_group_id``, excluding chatroom/workspace
        owners, and excludes shared groups with survivors), which left
        message-count triggers working only for single-member agent groups.
        Deduped like :meth:`list_layers_for_turn`; ranking is cosmetic here but
        kept for a stable order.
        """
        ids = list(dict.fromkeys(agent_ids))
        gc = t.graphrag_configs
        room_workspace = (
            sa.select(conv.chatrooms.c.workspace_id)
            .where(conv.chatrooms.c.id == chatroom_id)
            .scalar_subquery()
        )
        chatroom_layer = sa.select(gc, _member_agent_id()).where(
            sa.and_(
                gc.c.owner_kind == "chatroom",
                gc.c.owner_chatroom_id == chatroom_id,
                gc.c.deleted_at.is_(None),
            )
        )
        group_join = gc.join(ag.agent_groups, ag.agent_groups.c.id == gc.c.owner_agent_group_id).join(
            ag.agent_group_members,
            ag.agent_group_members.c.agent_group_id == ag.agent_groups.c.id,
        )
        group_layer = (
            sa.select(gc, _member_agent_id())
            .select_from(group_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "agent_group",
                    ag.agent_group_members.c.agent_id.in_(ids),
                    ag.agent_groups.c.concept_map_enabled.is_(True),
                    ag.agent_groups.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )
        workspace_join = gc.join(conv.workspaces, conv.workspaces.c.id == gc.c.owner_workspace_id)
        workspace_layer = (
            sa.select(gc, _member_agent_id())
            .select_from(workspace_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "workspace",
                    gc.c.owner_workspace_id == room_workspace,
                    conv.workspaces.c.concept_map_enabled.is_(True),
                    conv.workspaces.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )
        rows = (await self._db.execute(sa.union_all(chatroom_layer, group_layer, workspace_layer))).all()
        seen: set[uuid.UUID] = set()
        configs: list[GraphRagConfig] = []
        for r in rows:
            if r.id in seen:
                continue
            seen.add(r.id)
            configs.append(_row_to_config(r))
        configs.sort(key=lambda c: (_LAYER_RANK.get(c.owner_kind, 99), c.created_at))
        return configs

    async def list_layers_for_turn(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        """Every Concept Map covering ``agent_id`` in ``chatroom_id`` (WS4 R11.09).

        Returns, ordered narrow -> wide (chatroom, agent_group(s), workspace):

        - the chatroom-owned config (inherits the room ACL, always active);
        - each agent_group-owned config whose group the agent is a **live**
          member of *and* whose group has ``concept_map_enabled`` (AC-6/AC-11 —
          membership is read fresh, so a removed member loses access next turn);
        - the workspace-owned config for the room's workspace, when that
          workspace has ``concept_map_enabled``.

        Resolved in a single round-trip (UNION ALL) so the per-turn layer fan-out
        never becomes a per-group N+1; ranking is applied in Python.
        """
        gc = t.graphrag_configs
        room_workspace = (
            sa.select(conv.chatrooms.c.workspace_id)
            .where(conv.chatrooms.c.id == chatroom_id)
            .scalar_subquery()
        )
        chatroom_layer = sa.select(gc, _member_agent_id()).where(
            sa.and_(
                gc.c.owner_kind == "chatroom",
                gc.c.owner_chatroom_id == chatroom_id,
                gc.c.deleted_at.is_(None),
            )
        )
        group_join = gc.join(ag.agent_groups, ag.agent_groups.c.id == gc.c.owner_agent_group_id).join(
            ag.agent_group_members,
            ag.agent_group_members.c.agent_group_id == ag.agent_groups.c.id,
        )
        group_layer = (
            sa.select(gc, _member_agent_id())
            .select_from(group_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "agent_group",
                    ag.agent_group_members.c.agent_id == agent_id,
                    ag.agent_groups.c.concept_map_enabled.is_(True),
                    ag.agent_groups.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )
        workspace_join = gc.join(conv.workspaces, conv.workspaces.c.id == gc.c.owner_workspace_id)
        workspace_layer = (
            sa.select(gc, _member_agent_id())
            .select_from(workspace_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "workspace",
                    gc.c.owner_workspace_id == room_workspace,
                    conv.workspaces.c.concept_map_enabled.is_(True),
                    conv.workspaces.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )
        rows = (await self._db.execute(sa.union_all(chatroom_layer, group_layer, workspace_layer))).all()
        seen: set[uuid.UUID] = set()
        configs: list[GraphRagConfig] = []
        for r in rows:
            if r.id in seen:
                continue
            seen.add(r.id)
            configs.append(_row_to_config(r))
        configs.sort(key=lambda c: (_LAYER_RANK.get(c.owner_kind, 99), c.created_at))
        return configs

    async def list_coverage_for_agent(
        self,
        agent_id: uuid.UUID,
    ) -> Sequence[AgentConceptMapCoverage]:
        """Every Concept Map covering an agent, narrow -> wide (Phase 4α R11.09).

        The read-only transparency view for the agent Knowledge tab. Unlike
        :meth:`list_layers_for_turn` (turn-scoped to one chatroom, and excluding
        disabled wide layers), this is agent-scoped across *all* the agent's rooms
        and returns disabled wide maps too, flagged ``active=False`` — the map
        exists but is not currently fed to retrieval. Covers:

        - each chatroom the agent is in (``active`` always True — inherits the ACL);
        - each agent_group the agent is a live member of (``active`` = the group's
          ``concept_map_enabled``);
        - each workspace owning one of those chatrooms (``active`` = the workspace's
          ``concept_map_enabled``).

        One round-trip (UNION ALL); ranking + dedup applied in Python.
        """
        gc = t.graphrag_configs
        member = _member_agent_id()

        chatroom_join = gc.join(conv.chatrooms, conv.chatrooms.c.id == gc.c.owner_chatroom_id).join(
            conv.chatroom_agents,
            conv.chatroom_agents.c.chatroom_id == conv.chatrooms.c.id,
        )
        chatroom_layer = (
            sa.select(
                gc,
                member,
                conv.chatrooms.c.name.label("owner_name"),
                sa.true().label("active"),
            )
            .select_from(chatroom_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "chatroom",
                    conv.chatroom_agents.c.agent_id == agent_id,
                    conv.chatrooms.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )

        group_join = gc.join(ag.agent_groups, ag.agent_groups.c.id == gc.c.owner_agent_group_id).join(
            ag.agent_group_members,
            ag.agent_group_members.c.agent_group_id == ag.agent_groups.c.id,
        )
        group_layer = (
            sa.select(
                gc,
                member,
                ag.agent_groups.c.name.label("owner_name"),
                ag.agent_groups.c.concept_map_enabled.label("active"),
            )
            .select_from(group_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "agent_group",
                    ag.agent_group_members.c.agent_id == agent_id,
                    ag.agent_groups.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )

        agent_room_workspaces = (
            sa.select(conv.chatrooms.c.workspace_id)
            .select_from(
                conv.chatrooms.join(
                    conv.chatroom_agents,
                    conv.chatroom_agents.c.chatroom_id == conv.chatrooms.c.id,
                )
            )
            .where(
                sa.and_(
                    conv.chatroom_agents.c.agent_id == agent_id,
                    conv.chatrooms.c.deleted_at.is_(None),
                )
            )
        )
        workspace_join = gc.join(conv.workspaces, conv.workspaces.c.id == gc.c.owner_workspace_id)
        workspace_layer = (
            sa.select(
                gc,
                member,
                conv.workspaces.c.name.label("owner_name"),
                conv.workspaces.c.concept_map_enabled.label("active"),
            )
            .select_from(workspace_join)
            .where(
                sa.and_(
                    gc.c.owner_kind == "workspace",
                    gc.c.owner_workspace_id.in_(agent_room_workspaces),
                    conv.workspaces.c.deleted_at.is_(None),
                    gc.c.deleted_at.is_(None),
                )
            )
        )

        rows = (await self._db.execute(sa.union_all(chatroom_layer, group_layer, workspace_layer))).all()
        seen: set[uuid.UUID] = set()
        out: list[AgentConceptMapCoverage] = []
        for r in rows:
            if r.id in seen:
                continue
            seen.add(r.id)
            out.append(
                AgentConceptMapCoverage(
                    config=_row_to_config(r),
                    owner_name=r.owner_name,
                    active=bool(r.active),
                )
            )
        out.sort(key=lambda e: (_LAYER_RANK.get(e.config.owner_kind, 99), e.config.created_at))
        return out

    async def list_owner_options(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[ConceptMapOwnerOption]:
        """Owners in the project without a live Concept Map (Phase 4α picker).

        One query per owner kind: agent_groups and workspaces are project-scoped
        directly; chatrooms resolve their project via their workspace. Each excludes
        owners already owning a live config (the per-owner 1:1). Returns a flat list
        ordered by kind then name for a deterministic picker.
        """
        gc = t.graphrag_configs

        groups_used = sa.select(gc.c.owner_agent_group_id).where(
            sa.and_(
                gc.c.owner_kind == "agent_group",
                gc.c.deleted_at.is_(None),
                gc.c.owner_agent_group_id.isnot(None),
            )
        )
        group_rows = (
            await self._db.execute(
                sa.select(ag.agent_groups.c.id, ag.agent_groups.c.name)
                .where(
                    sa.and_(
                        ag.agent_groups.c.project_id == project_id,
                        ag.agent_groups.c.deleted_at.is_(None),
                        ag.agent_groups.c.id.notin_(groups_used),
                    )
                )
                .order_by(ag.agent_groups.c.name)
            )
        ).all()

        rooms_used = sa.select(gc.c.owner_chatroom_id).where(
            sa.and_(
                gc.c.owner_kind == "chatroom",
                gc.c.deleted_at.is_(None),
                gc.c.owner_chatroom_id.isnot(None),
            )
        )
        room_rows = (
            await self._db.execute(
                sa.select(conv.chatrooms.c.id, conv.chatrooms.c.name)
                .select_from(
                    conv.chatrooms.join(
                        conv.workspaces, conv.workspaces.c.id == conv.chatrooms.c.workspace_id
                    )
                )
                .where(
                    sa.and_(
                        conv.workspaces.c.project_id == project_id,
                        conv.chatrooms.c.deleted_at.is_(None),
                        conv.chatrooms.c.id.notin_(rooms_used),
                    )
                )
                .order_by(conv.chatrooms.c.name)
            )
        ).all()

        workspaces_used = sa.select(gc.c.owner_workspace_id).where(
            sa.and_(
                gc.c.owner_kind == "workspace",
                gc.c.deleted_at.is_(None),
                gc.c.owner_workspace_id.isnot(None),
            )
        )
        workspace_rows = (
            await self._db.execute(
                sa.select(conv.workspaces.c.id, conv.workspaces.c.name)
                .where(
                    sa.and_(
                        conv.workspaces.c.project_id == project_id,
                        conv.workspaces.c.deleted_at.is_(None),
                        conv.workspaces.c.id.notin_(workspaces_used),
                    )
                )
                .order_by(conv.workspaces.c.name)
            )
        ).all()

        out: list[ConceptMapOwnerOption] = []
        out.extend(
            ConceptMapOwnerOption(owner_kind="agent_group", owner_id=r.id, owner_name=r.name)
            for r in group_rows
        )
        out.extend(
            ConceptMapOwnerOption(owner_kind="chatroom", owner_id=r.id, owner_name=r.name) for r in room_rows
        )
        out.extend(
            ConceptMapOwnerOption(owner_kind="workspace", owner_id=r.id, owner_name=r.name)
            for r in workspace_rows
        )
        return out

    async def list_in_state(
        self,
        state: BuildState,
    ) -> Sequence[GraphRagConfig]:
        rows = (
            await self._db.execute(
                _config_select().where(
                    sa.and_(
                        t.graphrag_configs.c.last_build_state == state.value,
                        t.graphrag_configs.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]:
        stmt = sa.select(t.graphrag_configs.c.id)
        if not include_deleted:
            stmt = stmt.where(t.graphrag_configs.c.deleted_at.is_(None))
        rows = (await self._db.execute(stmt)).all()
        return {r.id for r in rows}

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
        built_at: Any = None,
    ) -> None:
        values: dict[str, Any] = {
            "last_build_state": state.value,
            "last_build_error": error,
        }
        # D10: an explicit ``built_at`` (the build's started-at watermark) takes
        # precedence; ``stamp_built_at`` remains for callers (the reconciler) that
        # only need "now" on a terminal recovery.
        if built_at is not None:
            values["last_build_at"] = built_at
        elif stamp_built_at:
            values["last_build_at"] = now()
        await self._db.execute(
            t.graphrag_configs.update().where(t.graphrag_configs.c.id == config_id).values(**values)
        )

    async def set_embed_pin(
        self,
        *,
        config_id: uuid.UUID,
        provider: str,
        model: str,
        dim: int,
    ) -> None:
        """Persist the project embedding pin (Phase 2a D2).

        Written by the config service after resolving the builder key group at
        create/update, and by the builder's self-pin on the first successful
        build of a legacy null-pin config. Keeps the caller's transaction.
        """
        await self._db.execute(
            t.graphrag_configs.update()
            .where(t.graphrag_configs.c.id == config_id)
            .values(embed_provider=provider, embed_model=model, embed_dim=dim)
        )

    async def soft_delete(self, config_id: uuid.UUID) -> None:
        # Clear all three owner columns, not just deleted_at: the partial
        # unique indexes on owner_chatroom_id/owner_agent_group_id/
        # owner_workspace_id (migration 0043_graphrag_owner.py) are only
        # `WHERE {col} IS NOT NULL`, not additionally scoped to
        # `deleted_at IS NULL` — a soft-deleted row with its owner column
        # still populated permanently blocks any future create() for that
        # same owner (409 GraphRagConfigAlreadyExists forever), even though
        # list_owner_options() already shows the owner as available again.
        await self._db.execute(
            t.graphrag_configs.update()
            .where(t.graphrag_configs.c.id == config_id)
            .values(
                deleted_at=now(),
                owner_chatroom_id=None,
                owner_agent_group_id=None,
                owner_workspace_id=None,
            )
        )

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID | None = None,
        trigger_config: dict[str, Any] | None = None,
        recency_half_life_days: float | None = None,
    ) -> None:
        """Partial update of a GraphRAG config (R11.05 — edit trigger / key-group).

        Only the fields the spec allows the user to mutate are accepted;
        ``agent_id`` is immutable post-create (1:1 with config in DB). A ``None``
        argument means "leave unchanged" (patch semantics), so the recency
        half-life cannot be cleared back to the platform default via update — a
        deliberate limitation (WS5); set a positive value to override it.
        """
        values: dict[str, Any] = {}
        if builder_key_group_id is not None:
            values["builder_key_group_id"] = builder_key_group_id
        if trigger_config is not None:
            values["trigger_config"] = trigger_config
        if recency_half_life_days is not None:
            values["recency_half_life_days"] = recency_half_life_days
        if not values:
            return
        await self._db.execute(
            t.graphrag_configs.update().where(t.graphrag_configs.c.id == config_id).values(**values)
        )


__all__ = ["GraphRagConfigRepository"]
