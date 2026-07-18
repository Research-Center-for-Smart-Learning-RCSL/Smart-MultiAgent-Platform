"""Shared wiring-test bootstrap: User -> Org -> Project -> Workspace -> KeyGroup -> Agent.

This exact chain was duplicated near-verbatim across test_agent_group_repository.py,
test_graphrag_owner_resolution.py, test_agent_group_service.py, and others before this
extraction (code-review finding on
docs/tasks/2026-07-16-context-token-cap-upper-bound/). New wiring tests that need a
minimal agent should use `seed_agent` here rather than re-inlining the chain; existing
call sites are left as-is to keep this change scoped to the file that introduced the
duplicate.
"""

from __future__ import annotations

import uuid

from contexts.agents.domain.models import AgentModelHint, ContextMode
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


async def seed_agent(db, *, email_prefix: str = "seed", **agent_overrides: object) -> uuid.UUID:
    """Bootstrap a project + key group and one agent in it; return the agent id.

    `agent_overrides` are merged onto a minimal default agent (general mode, no caps, no
    sampling controls) and passed to `AgentRepository.create` — e.g. pass
    `context_token_cap=5000` to override just that field.
    """
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"{email_prefix}-{u}@smap.test", password_hash="x" * 16)
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

    defaults: dict[str, object] = {
        "project_id": project.id,
        "name": f"agent-{u}",
        "model_hint": AgentModelHint.CLAUDE,
        "model_id": None,
        "effort": None,
        "key_group_id": kg.id,
        "system_prompt": "deterministic test agent",
        "rag_config_id": None,
        "knowmap_config_id": None,
        "context_mode": ContextMode.GENERAL,
        "context_token_cap": None,
        "skill_index_token_cap": None,
        "temperature": None,
        "top_p": None,
        "seed": None,
        "a2a_enabled": False,
        "wakeup_config": {},
        "workflow_capabilities": {},
    }
    defaults.update(agent_overrides)
    agent = await AgentRepository(db).create(**defaults)
    await db.commit()
    return agent.id
