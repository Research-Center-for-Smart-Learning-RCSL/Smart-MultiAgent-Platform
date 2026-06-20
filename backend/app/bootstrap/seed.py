"""Test-environment user + fixture seeding.

Runs at startup when `SMAP_APP_ENV=test` and `SMAP_SEED_ON_STARTUP=true`.
Idempotent: existing emails / names are left alone. Used by `compose.test.yml`
so Playwright E2E specs can log in and navigate seeded resources without
touching the email-verification flow.

Never enabled in prod: gated on `app.env == "test"`. Even if an operator
sets `SMAP_SEED_ON_STARTUP=true` in prod, the env check short-circuits.

Seeded IDs are written to `/tmp/e2e-seed-ids.env` so the CI bootstrap step
can export them as environment variables for the Playwright runner.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from contexts.agents.domain.models import (
    AgentModelHint,
    ContextMode,
    PromptStrategy,
)
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.infrastructure.repositories.chatroom_repo import (
    ChatroomRepository,
)
from contexts.conversation.infrastructure.repositories.workspace_repo import (
    WorkspaceRepository,
)
from contexts.identity.domain.models import UserStatus
from contexts.identity.infrastructure.repositories import (
    AdminRepository,
    UserRepository,
)
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from contexts.workflow.infrastructure.repositories import WorkflowRepository
from shared_kernel.auth.password import PasswordHasher
from shared_kernel.db.session import async_session

logger = logging.getLogger(__name__)

_SEED_IDS_PATH = Path("/tmp/e2e-seed-ids.env")  # noqa: S108 — test-only fixture output

_MINIMAL_WORKFLOW_DEFINITION: dict = {
    "nodes": [
        {
            "id": "trigger",
            "type": "trigger",
            "config": {"kind": "manual"},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "end",
            "type": "end",
            "config": {},
            "position": {"x": 200, "y": 0},
        },
    ],
    "edges": [
        {"source": "trigger", "target": "end"},
    ],
}


async def seed_test_users(*, app_env: str) -> None:
    if app_env != "test":
        return
    if os.environ.get("SMAP_SEED_ON_STARTUP", "").lower() not in {"1", "true", "yes"}:
        return

    seeds = [
        (
            os.environ.get("SMAP_SEED_USER_EMAIL", "e2e-user@smap.test"),
            os.environ.get("SMAP_SEED_USER_PASSWORD", "E2eP@ssw0rd!Str0ng"),
        ),
        (
            os.environ.get("SMAP_SEED_ADMIN_EMAIL", "e2e-admin@smap.test"),
            os.environ.get("SMAP_SEED_ADMIN_PASSWORD", "E2eAdm1n!Str0ng"),
        ),
    ]

    hasher = PasswordHasher()
    seed_ids: dict[str, str] = {}

    async with async_session() as db, db.begin():
        users = UserRepository(db)

        user_ids: list[uuid.UUID] = []
        for email, password in seeds:
            existing = await users.get_active_by_email(email)
            if existing is not None:
                logger.info("seed: user already present, skipping email=%s", email)
                user_ids.append(existing.id)
                continue
            user = await users.insert(
                email=email,
                password_hash=hasher.hash(password),
                status=UserStatus.PENDING,
            )
            if not await users.mark_verified(user.id):
                logger.warning("seed: mark_verified no-op for %s", email)
            else:
                logger.info("seed: created verified user %s", email)
            user_ids.append(user.id)

        regular_user_id = user_ids[0]
        admin_user_id = user_ids[1]
        seed_ids["E2E_TARGET_USER_ID"] = str(regular_user_id)

        admins = AdminRepository(db)
        try:
            await admins.promote(user_id=admin_user_id, promoted_by=None)
            logger.info("seed: promoted admin user %s", seeds[1][0])
        except Exception:
            logger.warning("seed: admin promotion failed for %s", seeds[1][0], exc_info=True)

        await _seed_fixtures(db, regular_user_id, seed_ids)

    _write_seed_ids(seed_ids)


async def _seed_fixtures(
    db: object,
    user_id: uuid.UUID,
    seed_ids: dict[str, str],
) -> None:
    """Create org → project → workspace → chatroom → agent → workflow.

    Idempotent: if an org named "E2E Test Org" already exists for this user,
    skips creation and resolves the existing entity IDs instead.
    """
    orgs = OrgRepository(db)  # type: ignore[arg-type]
    org_members = OrgMemberRepository(db)  # type: ignore[arg-type]
    projects = ProjectRepository(db)  # type: ignore[arg-type]
    project_members = ProjectMemberRepository(db)  # type: ignore[arg-type]
    workspaces = WorkspaceRepository(db)  # type: ignore[arg-type]
    chatrooms = ChatroomRepository(db)  # type: ignore[arg-type]
    key_groups = KeyGroupRepository(db)  # type: ignore[arg-type]
    agents_repo = AgentRepository(db)  # type: ignore[arg-type]
    workflows = WorkflowRepository(db)  # type: ignore[arg-type]

    existing_orgs = await orgs.list_for_user(user_id)
    e2e_org = next((o for o in existing_orgs if o.name == "E2E Test Org"), None)

    if e2e_org is not None:
        logger.info("seed: fixtures already exist (org %s), resolving IDs", e2e_org.id)
        seed_ids["E2E_ORG_ID"] = str(e2e_org.id)

        org_projects = await projects.list_by_org(e2e_org.id)
        proj = next((p for p in org_projects if p.name == "E2E Test Project"), None)
        if proj:
            seed_ids["E2E_PROJECT_ID"] = str(proj.id)
            ws_list = await workspaces.list_for_project(proj.id)
            ws = ws_list[0] if ws_list else None
            if ws:
                seed_ids["E2E_WORKSPACE_ID"] = str(ws.id)
                cr_list = await chatrooms.list_for_workspace(ws.id)
                if cr_list:
                    seed_ids["E2E_CHATROOM_ID"] = str(cr_list[0].id)
                wf_list = await workflows.list_for_workspace(ws.id)
                if wf_list:
                    seed_ids["E2E_WORKFLOW_ID"] = str(wf_list[0].id)

            agent_list = await agents_repo.list_for_project(proj.id)
            if agent_list:
                seed_ids["E2E_AGENT_ID"] = str(agent_list[0].id)

        required = {
            "E2E_PROJECT_ID",
            "E2E_WORKSPACE_ID",
            "E2E_CHATROOM_ID",
            "E2E_AGENT_ID",
            "E2E_WORKFLOW_ID",
        }
        if required.issubset(seed_ids):
            seed_ids["E2E_HAS_NOTIFICATIONS"] = "1"
            return

        logger.warning(
            "seed: org exists but intermediate fixtures missing (%s), recreating", required - seed_ids.keys()
        )

    if e2e_org is None:
        org = await orgs.create(name="E2E Test Org", creator_user_id=user_id)
        logger.info("seed: created org %s", org.id)
        await org_members.add(
            org_id=org.id,
            user_id=user_id,
            role=OrgMemberRole.OWNER,
            is_original_creator=True,
        )
    else:
        org = e2e_org
    seed_ids["E2E_ORG_ID"] = str(org.id)

    project = await projects.create(
        name="E2E Test Project",
        owner_user_id=None,
        owner_org_id=org.id,
        created_by_user_id=user_id,
    )
    seed_ids["E2E_PROJECT_ID"] = str(project.id)
    logger.info("seed: created project %s", project.id)

    try:
        await project_members.add(
            project_id=project.id,
            user_id=user_id,
            role=ProjectMemberRole.OWNER,
        )
    except IntegrityError:
        logger.debug("seed: project member already exists, skipping")

    workspace = await workspaces.create(
        project_id=project.id,
        name="E2E Workspace",
    )
    seed_ids["E2E_WORKSPACE_ID"] = str(workspace.id)
    logger.info("seed: created workspace %s", workspace.id)

    chatroom = await chatrooms.create(
        workspace_id=workspace.id,
        name="E2E Chatroom",
    )
    seed_ids["E2E_CHATROOM_ID"] = str(chatroom.id)
    logger.info("seed: created chatroom %s", chatroom.id)

    key_group = await key_groups.create(
        project_id=project.id,
        name="E2E Key Group",
    )
    logger.info("seed: created key group %s", key_group.id)

    agent = await agents_repo.create(
        project_id=project.id,
        name="E2E Agent",
        model_hint=AgentModelHint.OPENAI,
        model_id=None,
        key_group_id=key_group.id,
        system_prompt="You are a test agent for E2E testing.",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        graphrag_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    seed_ids["E2E_AGENT_ID"] = str(agent.id)
    logger.info("seed: created agent %s", agent.id)

    workflow = await workflows.insert(
        workspace_id=workspace.id,
        name="E2E Workflow",
        definition=_MINIMAL_WORKFLOW_DEFINITION,
    )
    seed_ids["E2E_WORKFLOW_ID"] = str(workflow.id)
    logger.info("seed: created workflow %s", workflow.id)

    seed_ids["E2E_HAS_NOTIFICATIONS"] = "1"


def _write_seed_ids(seed_ids: dict[str, str]) -> None:
    """Write seed IDs as KEY=VALUE lines for shell sourcing."""
    lines = [f"{k}={v}" for k, v in sorted(seed_ids.items())]
    try:
        _SEED_IDS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("seed: wrote %d IDs to %s", len(lines), _SEED_IDS_PATH)
    except OSError:
        logger.error("seed: FAILED to write %s — E2E tests will skip", _SEED_IDS_PATH)
        raise


__all__ = ["seed_test_users"]
