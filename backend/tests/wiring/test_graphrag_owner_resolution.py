"""Wiring tier — GraphRAG owner resolution (Phase 1 + Phase 2b WS2, R11.05/R11.07).

Exercises the discriminated owner model against **real** Postgres. Phase 2b WS2
makes create owner-centric: a config is created for an existing ``agent_group``
(populated via the member-CRUD service), not by auto-wrapping a single agent.
The ownership-resolution invariant still holds: ``list_for_agents`` resolves a
config through the group membership join, so the owning agent sees its config
and an unrelated agent sees nothing.

The three GraphRAG I/O ports (Neo4j, Qdrant, embedder) are never touched here —
config CRUD is pure Postgres.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agents.domain.models import AgentModelHint, ContextMode, PromptStrategy
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.application.workspace_service import WorkspaceService
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    WorkspaceRepository,
)
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.domain.errors import GraphRagOwnerProjectMismatch
from contexts.knowledge.domain.graphrag import GraphRagConfigDraft
from contexts.knowledge.infrastructure import graphrag_tables as gt
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring


async def _group_with_member(
    db, *, project_id: uuid.UUID, user_id: uuid.UUID, agent_id: uuid.UUID
) -> uuid.UUID:
    svc = AgentGroupService(db)
    gid = await svc.create_group(
        project_id=project_id, name=f"grp-{uuid.uuid4().hex[:8]}", actor_user_id=user_id, actor_ip=None
    )
    await svc.add_member(group_id=gid, agent_id=agent_id, actor_user_id=user_id, actor_ip=None)
    return gid


async def _seed_project(db) -> SimpleNamespace:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"gr-{u}@smap.test", password_hash="x" * 16)
    org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=user.id)
    await OrgMemberRepository(db).add(
        org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
    )
    project = await ProjectRepository(db).create(
        name=f"proj-{u}",
        owner_user_id=None,
        owner_org_id=org.id,
        created_by_user_id=user.id,
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id, user_id=user.id, role=ProjectMemberRole.OWNER
    )
    workspace = await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    return SimpleNamespace(user=user, org=org, project=project, workspace=workspace)


async def _seed_agent(db, project_id: uuid.UUID, key_group_id: uuid.UUID) -> uuid.UUID:
    agent = await AgentRepository(db).create(
        project_id=project_id,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=key_group_id,
        system_prompt="deterministic test agent",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def test_create_for_agent_group_owner_sets_owner_columns() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        gid = await _group_with_member(db, project_id=env.project.id, user_id=env.user.id, agent_id=agent_id)
        await db.commit()

        cfg = await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        # The owning agent is still derived from the group's (single) membership.
        assert cfg.agent_id == agent_id

        row = (
            await db.execute(
                sa.select(
                    gt.graphrag_configs.c.owner_kind,
                    gt.graphrag_configs.c.owner_agent_group_id,
                ).where(gt.graphrag_configs.c.id == cfg.id)
            )
        ).one()
        assert row.owner_kind == "agent_group"
        assert row.owner_agent_group_id == gid


async def test_list_for_agents_resolves_through_membership() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        owner_agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        other_agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        gid = await _group_with_member(
            db, project_id=env.project.id, user_id=env.user.id, agent_id=owner_agent_id
        )
        await db.commit()

        cfg = await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)

        # A member agent resolves its config through the membership join.
        owned = await repo.list_for_agents([owner_agent_id])
        assert [c.id for c in owned] == [cfg.id]

        # An unrelated agent (no group membership) sees nothing.
        assert await repo.list_for_agents([other_agent_id]) == []


async def test_list_for_agents_excludes_shared_config_with_other_live_members() -> None:
    # Regression: deleting one member of a multi-member agent_group must not
    # resolve (and so must not soft-delete/purge) the group's shared Concept
    # Map while another live member still depends on it. The config is only
    # returned once removing the given agent(s) would leave zero live members.
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        agent_a = await _seed_agent(db, env.project.id, consumer_kg.id)
        agent_b = await _seed_agent(db, env.project.id, consumer_kg.id)
        gid = await _group_with_member(db, project_id=env.project.id, user_id=env.user.id, agent_id=agent_a)
        await AgentGroupService(db).add_member(
            group_id=gid, agent_id=agent_b, actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()

        cfg = await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)

        # Agent B is still a live member -> deleting agent A alone must not
        # resolve the shared config (it would otherwise be destroyed out from
        # under B).
        assert await repo.list_for_agents([agent_a]) == []
        assert await repo.list_for_agents([agent_b]) == []

        # Soft-delete agent B (as the real delete route would) -> now agent A
        # is the group's only live member, so deleting A empties the group and
        # the shared config is correctly resolved for cleanup.
        await AgentRepository(db).soft_delete(agent_id=agent_b, expected_version=1)
        await db.commit()

        assert [c.id for c in await repo.list_for_agents([agent_a])] == [cfg.id]

        # Deleting both members in one call also empties the group.
        assert [c.id for c in await repo.list_for_agents([agent_a, agent_b])] == [cfg.id]


async def test_create_for_chatroom_and_workspace_owners() -> None:
    # AC-3: chatroom- and workspace-owned configs can be created; the owner
    # columns are set for the discriminated kind.
    async with async_session() as db:
        env = await _seed_project(db)
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        room = await ChatroomRepository(db).create(workspace_id=env.workspace.id, name="r")
        await db.commit()

        svc = GraphRagConfigService(db)
        room_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="chatroom", owner_id=room.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        ws_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="workspace", owner_id=env.workspace.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        assert room_cfg.owner_kind == "chatroom"
        assert room_cfg.owner_chatroom_id == room.id
        assert room_cfg.agent_id is None  # a wide owner has no derived agent
        assert ws_cfg.owner_kind == "workspace"
        assert ws_cfg.owner_workspace_id == env.workspace.id


async def test_recreate_after_soft_delete_does_not_collide_on_owner_index() -> None:
    # Regression (code review finding): soft_delete used to leave the owner
    # column populated, and the partial unique index on it is not scoped to
    # `deleted_at IS NULL` -- so a soft-deleted config permanently blocked
    # any future create() for that same owner (409 GraphRagConfigAlreadyExists
    # forever), even though the owner was shown as available again.
    async with async_session() as db:
        env = await _seed_project(db)
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        room = await ChatroomRepository(db).create(workspace_id=env.workspace.id, name="r")
        await db.commit()

        svc = GraphRagConfigService(db)
        first = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="chatroom", owner_id=room.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        await GraphRagConfigRepository(db).soft_delete(first.id)
        await db.commit()

        # Must not raise GraphRagConfigAlreadyExists.
        second = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="chatroom", owner_id=room.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        assert second.id != first.id
        assert second.owner_chatroom_id == room.id


async def test_list_layers_for_turn_orders_and_gates_layers() -> None:
    # AC-4/AC-6/AC-11: the resolver returns the chatroom + enabled agent_group +
    # enabled workspace layers ordered narrow->wide; wide layers are excluded
    # while disabled; a removed member loses the group layer on the next resolve.
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        room = await ChatroomRepository(db).create(workspace_id=env.workspace.id, name="r")
        gid = await _group_with_member(db, project_id=env.project.id, user_id=env.user.id, agent_id=agent_id)
        await db.commit()

        svc = GraphRagConfigService(db)
        room_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="chatroom", owner_id=room.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        group_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        ws_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="workspace", owner_id=env.workspace.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)

        # Wide layers are disabled by default (concept_map_enabled=false) — only
        # the chatroom map covers the turn.
        layers = await repo.list_layers_for_turn(agent_id=agent_id, chatroom_id=room.id)
        assert [c.id for c in layers] == [room_cfg.id]

        # Enable both wide layers — they now appear, ordered narrow->wide.
        await AgentGroupService(db).set_concept_map_enabled(
            group_id=gid, enabled=True, actor_user_id=env.user.id, actor_ip=None
        )
        await WorkspaceService(db).set_concept_map_enabled(
            workspace_id=env.workspace.id, enabled=True, actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()

        layers = await repo.list_layers_for_turn(agent_id=agent_id, chatroom_id=room.id)
        assert [c.id for c in layers] == [room_cfg.id, group_cfg.id, ws_cfg.id]
        assert [c.owner_kind for c in layers] == ["chatroom", "agent_group", "workspace"]

        # AC-11: removing the agent from the group revokes the group layer on the
        # very next resolve (live membership, not a cached set).
        await AgentGroupService(db).remove_member(
            group_id=gid, agent_id=agent_id, actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()

        layers = await repo.list_layers_for_turn(agent_id=agent_id, chatroom_id=room.id)
        assert [c.id for c in layers] == [room_cfg.id, ws_cfg.id]


async def test_create_rejects_owner_from_another_project() -> None:
    # AC-3: an owner that does not live in the config's project is rejected
    # (RFC 7807 GraphRagOwnerProjectMismatch).
    async with async_session() as db:
        env = await _seed_project(db)
        other = await _seed_project(db)
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        foreign_room = await ChatroomRepository(db).create(workspace_id=other.workspace.id, name="x")
        await db.commit()

        with pytest.raises(GraphRagOwnerProjectMismatch):
            await GraphRagConfigService(db).create(
                project_id=env.project.id,
                draft=GraphRagConfigDraft(
                    owner_kind="chatroom", owner_id=foreign_room.id, builder_key_group_id=builder_kg.id
                ),
                actor_user_id=env.user.id,
                actor_ip=None,
            )
