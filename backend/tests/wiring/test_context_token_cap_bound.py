"""`agents_context_cap_bounded` DB CHECK (task dossier:
docs/tasks/2026-07-16-context-token-cap-upper-bound/, AC-3).

The API's `le=MAX_CONTEXT_TOKEN_CAP` (AC-1/AC-2, unit-tested in
`test_agents_api_models.py`) is the intended gate; this proves the DB-level backstop
mirroring `agents_skill_index_cap_bounded` also holds for a write that bypasses the API
entirely — the same shape as 0056's sibling constraint.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from contexts.agents.domain.models import MAX_CONTEXT_TOKEN_CAP, AgentModelHint, ContextMode
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.infrastructure.repositories import WorkspaceRepository
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring


async def _seed_agent(db) -> uuid.UUID:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"ctc-{u}@smap.test", password_hash="x" * 16)
    org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=user.id)
    await OrgMemberRepository(db).add(
        org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
    )
    project = await ProjectRepository(db).create(
        name=f"proj-{u}", owner_user_id=None, owner_org_id=org.id, created_by_user_id=user.id
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id, user_id=user.id, role=ProjectMemberRole.OWNER
    )
    await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    kg = await KeyGroupRepository(db).create(project_id=project.id, name="consumer")
    agent = await AgentRepository(db).create(
        project_id=project.id,
        name=f"agent-{u}",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=kg.id,
        system_prompt="deterministic test agent",
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        skill_index_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    await db.commit()
    return agent.id


async def test_direct_update_above_the_bound_raises_integrity_error() -> None:
    async with async_session() as db:
        agent_id = await _seed_agent(db)

        stmt = sa.text("UPDATE agents SET context_token_cap = :cap WHERE id = :id")
        params = {"cap": MAX_CONTEXT_TOKEN_CAP + 1, "id": agent_id}
        with pytest.raises(IntegrityError, match="agents_context_cap_bounded"):
            await db.execute(stmt, params)


async def test_direct_update_at_the_bound_succeeds() -> None:
    async with async_session() as db:
        agent_id = await _seed_agent(db)

        await db.execute(
            sa.text("UPDATE agents SET context_token_cap = :cap WHERE id = :id"),
            {"cap": MAX_CONTEXT_TOKEN_CAP, "id": agent_id},
        )
        await db.commit()

        row = await db.execute(
            sa.text("SELECT context_token_cap FROM agents WHERE id = :id"), {"id": agent_id}
        )
        assert row.scalar_one() == MAX_CONTEXT_TOKEN_CAP
