"""Wiring tier — GraphRAG owner decoupling (Phase 1 M1/M2, R11.05/R11.07).

Exercises the discriminated owner model against **real** Postgres. The expand
migration (0043) plus the M2 service/repository rewrite must satisfy one
invariant above all: resolving a config *by ownership* (the agent's singleton
``agent_group`` membership) returns exactly what the legacy ``WHERE agent_id
IN (:ids)`` returned, so the contract step can drop ``agent_id`` without any
behavior change.

Covers:
  1. ``GraphRagConfigService.create`` wraps the agent in a singleton
     ``agent_group`` (member = the agent) and dual-writes owner + legacy
     ``agent_id`` on the config row.
  2. ``list_for_agents`` resolves through the membership join and matches the
     legacy ``agent_id`` scope: the owning agent sees its config, an unrelated
     agent sees nothing.

The three GraphRAG I/O ports (Neo4j, Qdrant, embedder) are never touched here —
config CRUD is pure Postgres.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from contexts.agent_groups.infrastructure import tables as ag
from contexts.agents.domain.models import AgentModelHint, ContextMode, PromptStrategy
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.infrastructure.repositories import WorkspaceRepository
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
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
    await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    return SimpleNamespace(user=user, org=org, project=project)


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
        graphrag_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def test_create_wraps_agent_in_singleton_owner_group() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        await db.commit()

        cfg = await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(agent_id=agent_id, builder_key_group_id=builder_kg.id),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        # Legacy agent_id is dual-written through the expand phase.
        assert cfg.agent_id == agent_id

        # The owner columns point at a singleton agent_group.
        row = (
            await db.execute(
                sa.select(
                    gt.graphrag_configs.c.owner_kind,
                    gt.graphrag_configs.c.owner_agent_group_id,
                    gt.graphrag_configs.c.agent_id,
                ).where(gt.graphrag_configs.c.id == cfg.id)
            )
        ).one()
        assert row.owner_kind == "agent_group"
        assert row.owner_agent_group_id is not None
        assert row.agent_id == agent_id

        # Exactly one member — the former owning agent.
        members = (
            await db.execute(
                sa.select(ag.agent_group_members.c.agent_id).where(
                    ag.agent_group_members.c.agent_group_id == row.owner_agent_group_id
                )
            )
        ).all()
        assert [m.agent_id for m in members] == [agent_id]


async def test_list_for_agents_matches_legacy_agent_id_scope() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        owner_agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        other_agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        await db.commit()

        cfg = await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(agent_id=owner_agent_id, builder_key_group_id=builder_kg.id),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)

        # The owning agent resolves its config through the membership join —
        # exactly what `WHERE agent_id IN (owner_agent_id)` returned pre-decouple.
        owned = await repo.list_for_agents([owner_agent_id])
        assert [c.id for c in owned] == [cfg.id]

        # An unrelated agent (no owner-group membership) sees nothing.
        assert await repo.list_for_agents([other_agent_id]) == []
